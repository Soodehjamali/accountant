"""Bot Service -- supervisor that starts/stops the Telegram and Bale bots.

Run with::

    python -m bots.bot_service              # start both configured bots
    python -m bots.bot_service --only telegram
    python -m bots.bot_service --only bale
    python -m bots.bot_service --check      # print which platforms are configured

Lifecycle (Phase 11):
    * Each platform runs as its own child process
      (``python -m bots.telegram_bot`` / ``python -m bots.bale_bot``), so a
      crash in one platform never takes down the other, and the existing
      per-platform entry points stay the canonical way to run a single bot.
    * This supervisor reports the REAL process state to the backend via
      the runtime heartbeat endpoint: ``RUNNING`` while the child lives,
      ``ERROR`` when it crashes, ``STOPPED`` on shutdown.  The ERP admin
      UI derives its status badge exclusively from these reports -- status
      is never faked.
    * Children are automatically restarted after an unexpected exit
      (with a short backoff).

Live configuration sync (Admin Bot Settings):
    The admin UI stores tokens in the backend (encrypted ``bot_config``
    rows).  Every ``SYNC_INTERVAL_SECONDS`` this supervisor re-resolves each
    platform's effective token (backend config first, env-var fallback) and:

        * starts a platform's bot when it becomes configured/enabled --
          no supervisor restart needed after saving the token in the ERP,
        * stops the platform's bot when it is disabled or its token is
          cleared,
        * performs a **controlled restart of only that platform's child**
          when the token changes, so the running bot always uses the
          current backend-managed token.  The ERP backend and the other
          platform are never restarted.

    The admin never copies a token into .env after entering it in the UI.

Desktop note:
    Opening the Electron app does NOT start these bots.  The ERP backend
    and the bots are separate processes.  The intended flow is documented
    in docs/BOT_SETUP.md: start the backend, then run this supervisor
    (or configure it as a service).  The admin UI shows live status.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time

import httpx

from bots.config import (
    get_bot_api_base_url,
    get_bot_runtime_secret,
    resolve_platform_token,
)

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

#: How often the supervisor re-resolves the desired platform configuration
#: (token) and reports RUNNING.  The admin UI treats a missing heartbeat
#: older than 90s as STOPPED, so 15s gives several missed beats before a
#: false STOPPED.
SYNC_INTERVAL_SECONDS = 15

#: Restart backoff after a crash.
RESTART_BACKOFF_SECONDS = 5

#: How long to wait for a child to exit cleanly before killing it.
CHILD_STOP_TIMEOUT_SECONDS = 5


# ---------------------------------------------------------------------------
# Backend runtime reporting
# ---------------------------------------------------------------------------

#: Whether the last report to the backend succeeded (per platform) -- used to
#: log a warning only once per outage instead of spamming every sync tick.
_last_report_ok: dict[str, bool] = {}


def report_status(platform: str, status: str) -> None:
    """POST the runtime status of a platform to the backend (best-effort)."""
    try:
        httpx.post(
            f"{get_bot_api_base_url()}/api/v1/bot-config/{platform}/runtime",
            json={"status": status},
            headers={"X-Bot-Runtime-Secret": get_bot_runtime_secret()},
            timeout=10.0,
        )
        _last_report_ok[platform] = True
    except Exception:  # noqa: BLE001 - backend may be down; status will be stale
        if _last_report_ok.get(platform, True):
            logger.warning("Failed to report %s status '%s' to backend", platform, status)
        _last_report_ok[platform] = False


async def _report_status_async(platform: str, status: str) -> None:
    """``report_status`` without blocking the event loop (shutdown-safe)."""
    await asyncio.to_thread(report_status, platform, status)


def platform_configured(platform: str) -> bool:
    """Return True when the platform has a token (backend config or env)."""
    return resolve_platform_token(platform) is not None


# ---------------------------------------------------------------------------
# Process supervision
# ---------------------------------------------------------------------------

async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    """Wait up to ``seconds``; return False when shutdown was requested."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return True  # timer elapsed, keep going
    return False  # stop requested


async def _wait_child(
    proc: asyncio.subprocess.Process,
    stop_event: asyncio.Event,
    timeout: float,
) -> bool:
    """Wait up to ``timeout`` for ``proc`` to exit.

    Returns True when the child exited (unexpectedly -- shutdown is handled
    by the caller via ``stop_event``), False when the timeout elapsed with
    the child still alive.
    """
    deadline = time.monotonic() + timeout
    while not stop_event.is_set():
        if proc.returncode is not None:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=min(0.5, remaining))
        except asyncio.TimeoutError:
            continue
    return False


async def _terminate_child(proc: asyncio.subprocess.Process) -> None:
    """Gracefully stop a child, escalating to kill after a short timeout."""
    if proc is None or proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=CHILD_STOP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


async def _spawn_and_watch(platform: str, stop_event: asyncio.Event) -> None:
    """Run the platform's bot process, live-synced to the backend config.

    Re-resolves the platform token every ``SYNC_INTERVAL_SECONDS`` and
    starts/stops/restarts only this platform's child so the running bot
    always uses the token currently saved in the ERP admin UI.
    """
    module = f"bots.{platform}_bot"
    cmd = [sys.executable, "-m", module]

    proc: asyncio.subprocess.Process | None = None
    active_token: str | None = None

    while not stop_event.is_set():
        token = await asyncio.to_thread(resolve_platform_token, platform)

        if token is None:
            # Platform disabled or token cleared: stop the child if it is
            # running, then keep polling -- the admin may re-enable it later
            # without a supervisor restart.
            if proc is not None and proc.returncode is None:
                logger.info("[%s] platform disabled or token cleared; stopping bot process", platform)
                await _terminate_child(proc)
                await _report_status_async(platform, "STOPPED")
            proc = None
            active_token = None
            if not await _wait_or_stop(stop_event, SYNC_INTERVAL_SECONDS):
                break
            continue

        # Token changed while running -> controlled restart of JUST this
        # platform's bot process (the child re-reads the token at boot).
        if proc is not None and proc.returncode is None and token != active_token:
            logger.info("[%s] bot token changed; restarting bot process to pick it up", platform)
            await _terminate_child(proc)
            proc = None

        if proc is None or proc.returncode is not None:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=None,
                stderr=None,
            )
            active_token = token
            logger.info("[%s] bot process started (pid=%s)", platform, proc.pid)
            # Brief pause so the child can boot before the next token poll.
            if not await _wait_or_stop(stop_event, 1.0):
                break
            continue

        # Healthy child running with the current token.
        await _report_status_async(platform, "RUNNING")
        exited = await _wait_child(proc, stop_event, SYNC_INTERVAL_SECONDS)
        if stop_event.is_set():
            break
        if exited:
            logger.warning(
                "[%s] bot process exited unexpectedly (code=%s)", platform, proc.returncode
            )
            await _report_status_async(platform, "ERROR")
            proc = None
            active_token = None
            if not await _wait_or_stop(stop_event, RESTART_BACKOFF_SECONDS):
                break

    if proc is not None and proc.returncode is None:
        await _terminate_child(proc)
    await _report_status_async(platform, "STOPPED")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _run(platforms: list[str]) -> int:
    stop_event = asyncio.Event()

    def _requested_shutdown(signum, _frame) -> None:  # noqa: ANN001
        logger.info("Shutdown requested (%s)", signal.Signals(signum).name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _requested_shutdown, sig, None)
        except NotImplementedError:  # Windows: no add_signal_handler for SIGTERM
            signal.signal(sig, _requested_shutdown)

    tasks = [asyncio.create_task(_spawn_and_watch(p, stop_event)) for p in platforms]
    await asyncio.gather(*tasks)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ERP bot service supervisor")
    parser.add_argument("--only", choices=["telegram", "bale"], default=None,
                        help="Run only this platform's bot")
    parser.add_argument("--check", action="store_true",
                        help="Print which platforms are configured and exit")
    args = parser.parse_args()

    if args.check:
        for platform in ("telegram", "bale"):
            logger.info("%s: %s", platform, "configured" if platform_configured(platform) else "not configured")
        return 0

    platforms = ["telegram", "bale"] if args.only is None else [args.only]
    configured = [p for p in platforms if platform_configured(p)]
    if not configured:
        logger.info(
            "No bot tokens configured yet. Waiting for configuration -- save a "
            "token in the ERP admin (Settings -> Bots & Messaging) and this "
            "supervisor will start the bot automatically. (Or set "
            "TELEGRAM_BOT_TOKEN / BALE_BOT_TOKEN as a dev fallback.)"
        )

    try:
        return asyncio.run(_run(platforms))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())