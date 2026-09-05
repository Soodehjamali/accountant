"""Bot configuration service (per-platform Telegram/Bale settings).

Per ``services/__init__.py``'s convention, every function here takes an
already-open ``Session`` and never commits/closes it -- the caller's job.

Responsibilities:
    * Encrypt/decrypt bot tokens at rest (Fernet, key derived from the
      application ``SECRET_KEY``).  Only the ciphertext and a 4-char hint
      are persisted; the raw token is never stored and never returned by
      admin-facing reads.
    * Save/retrieve per-platform config (``bot_config`` rows).
    * ``test_connection`` -- validate a token against the platform's own
      ``getMe`` API (Telegram / Bale).  No credentials are required beyond
      the token itself.
    * Runtime status -- derived from the heartbeat the actual bot process
      reports (``set_runtime_status``).  Status is never faked: if no
      process has reported, the platform is ``STOPPED``; if a process
      reported an error, it is ``ERROR``.

Secret handling:
    * The encryption key is derived from ``SECRET_KEY`` (the same HMAC key
      used for JWTs).  Rotating ``SECRET_KEY`` makes previously stored
      tokens undecryptable -- the admin simply re-saves the token.
    * Tokens never appear in responses, logs, or the frontend (only the
      last-4-char hint is exposed).
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.bot_config import BotConfig
from database.models.bot_platform_ref import BotPlatformRef
from services import bootstrap_service


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PlatformNotFoundError(LookupError):
    """Raised when a ``platform_code`` has no matching ``bot_platform_ref`` row."""

    def __init__(self, platform_code: str) -> None:
        super().__init__(f"No bot_platform_ref with code '{platform_code}' exists.")
        self.platform_code = platform_code


class ConfigNotFoundError(LookupError):
    """Raised when no ``bot_config`` row exists for a platform."""

    def __init__(self, platform_code: str) -> None:
        super().__init__(f"No bot_config row for platform '{platform_code}' exists.")
        self.platform_code = platform_code


class InvalidRuntimeStatusError(ValueError):
    """Raised when ``set_runtime_status`` gets an unknown status token."""

    def __init__(self, status: str) -> None:
        super().__init__(
            f"'{status}' is not a valid bot runtime status; use one of "
            f"{sorted(RUNTIME_STATUSES)}."
        )
        self.status = status


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Platform codes understood by this service (must exist in bot_platform_ref).
SUPPORTED_PLATFORMS = frozenset({"TELEGRAM", "BALE"})

#: Status tokens reported by the bot processes (and stored in bot_config).
RUNTIME_STATUSES = frozenset({"RUNNING", "STOPPED", "ERROR"})

#: A heartbeat older than this means the bot process is presumed down.
HEARTBEAT_STALE_AFTER = datetime.timedelta(seconds=90)

#: Platform API base URLs for connection testing (getMe).
_API_BASES = {
    "TELEGRAM": "https://api.telegram.org",
    "BALE": "https://tapi.bale.ai",
}


# ---------------------------------------------------------------------------
# Token encryption (Fernet, key derived from SECRET_KEY)
# ---------------------------------------------------------------------------

def _fernet(secret_key: str):
    """Return a Fernet cipher bound to ``secret_key``.

    The key is SHA-256(secret_key) url-safe-base64-encoded -- a stable,
    32-byte Fernet key derived from the app's existing ``SECRET_KEY`` so no
    new secret must be provisioned.
    """
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_token(token: str, *, secret_key: str) -> str:
    """Return the Fernet ciphertext of ``token``."""
    return _fernet(secret_key).encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str, *, secret_key: str) -> str:
    """Return the plaintext token for ``ciphertext``.

    Raises:
        ValueError: decryption failed (e.g. SECRET_KEY rotated).
    """
    return _fernet(secret_key).decrypt(ciphertext.encode("ascii")).decode("utf-8")


# ---------------------------------------------------------------------------
# Platform / row helpers
# ---------------------------------------------------------------------------

def _get_platform(session: Session, platform_code: str) -> BotPlatformRef:
    platform = session.execute(
        select(BotPlatformRef).where(BotPlatformRef.code == platform_code)
    ).scalar_one_or_none()
    if platform is None:
        raise PlatformNotFoundError(platform_code)
    return platform


def _ensure_config(
    session: Session,
    platform_code: str,
    *,
    created_by: uuid.UUID,
) -> BotConfig:
    """Return the ``bot_config`` row for a platform, creating it if absent."""
    platform = _get_platform(session, platform_code)
    existing = session.execute(
        select(BotConfig).where(BotConfig.bot_platform_id == platform.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    config = BotConfig(
        bot_platform_id=platform.id,
        enabled=False,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(config)
    session.flush()
    return config


def _token_hint(token: str) -> str:
    """Return the last 4 characters of a token for display."""
    return token[-4:] if len(token) >= 4 else token


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_config(session: Session, platform_code: str) -> BotConfig | None:
    """Return the ``bot_config`` row for a platform, or ``None``.

    A fresh database has no ``bot_platform_ref`` rows until the first save or
    heartbeat seeds them; this read path seeds them too so the admin list
    endpoint works before any bot has ever been configured (instead of
    raising ``PlatformNotFoundError`` -> HTTP 500).
    """
    try:
        platform = _get_platform(session, platform_code)
    except PlatformNotFoundError:
        system_user = bootstrap_service.ensure_system_user(session)
        bootstrap_service.ensure_bot_platforms(session, system_user.id)
        platform = _get_platform(session, platform_code)
    return session.execute(
        select(BotConfig).where(BotConfig.bot_platform_id == platform.id)
    ).scalar_one_or_none()


def save_config(
    session: Session,
    platform_code: str,
    *,
    enabled: bool,
    updated_by: uuid.UUID,
    token: str | None = None,
    secret_key: str,
) -> BotConfig:
    """Save (create or update) the config for a platform.

    ``token`` replaces the stored token only when provided -- admin reads
    never reveal the current token, so an update without a new token keeps
    the existing secret.  Empty strings are treated as "clear the token".

    Changing or clearing the token also clears the cached bot identity
    (``bot_username`` / ``bot_name`` / ``bot_id``): the old identity may
    belong to a different bot, and the next successful connection test
    repopulates it.
    """
    if platform_code not in SUPPORTED_PLATFORMS:
        raise PlatformNotFoundError(platform_code)

    # Seed platforms on first use so a fresh database works out of the box.
    system_user = bootstrap_service.ensure_system_user(session)
    bootstrap_service.ensure_bot_platforms(session, system_user.id)

    config = _ensure_config(session, platform_code, created_by=updated_by)
    config.enabled = enabled
    config.updated_by = updated_by

    if token is not None:
        if token == "":
            config.token_ciphertext = None
            config.token_hint = None
            _clear_identity(config)
        else:
            config.token_ciphertext = encrypt_token(token, secret_key=secret_key)
            config.token_hint = _token_hint(token)
            # A new token may belong to a different bot -- drop stale identity.
            if config.bot_username is not None or config.bot_name is not None:
                _clear_identity(config)
    session.flush()
    return config


def _clear_identity(config: BotConfig) -> None:
    """Clear the cached getMe identity fields on a row."""
    config.bot_username = None
    config.bot_name = None
    config.bot_id = None


def set_identity(
    session: Session,
    platform_code: str,
    *,
    bot_id: str | None,
    username: str | None,
    name: str | None,
    updated_by: uuid.UUID,
) -> BotConfig:
    """Persist the bot identity reported by a successful getMe test.

    ``username`` is stored without the leading ``@``.  Callers should pass
    only values actually returned by the platform API -- never guess them.
    """
    config = _ensure_config(session, platform_code, created_by=updated_by)
    config.bot_username = username
    config.bot_name = name
    config.bot_id = bot_id
    config.updated_by = updated_by
    session.flush()
    return config


def get_plain_token(
    session: Session,
    platform_code: str,
    *,
    secret_key: str,
    require_enabled: bool = False,
) -> str | None:
    """Return the decrypted token for a platform, or ``None``.

    When ``require_enabled`` is true (the bot-process startup path), a
    platform that is disabled or has no stored token yields ``None``.
    """
    config = get_config(session, platform_code)
    if config is None or config.token_ciphertext is None:
        return None
    if require_enabled and not config.enabled:
        return None
    try:
        return decrypt_token(config.token_ciphertext, secret_key=secret_key)
    except Exception:  # noqa: BLE001 - undecryptable (rotated key) -> no token
        return None


def set_runtime_status(
    session: Session,
    platform_code: str,
    *,
    status: str,
    updated_by: uuid.UUID,
    secret_key: str,
) -> BotConfig:
    """Record a runtime-status heartbeat from the bot process.

    ``status`` must be one of ``RUNTIME_STATUSES``.  ``last_heartbeat`` is
    stamped ``now()`` on every call.  The admin status view derives
    Running/Stopped/Error from these real process reports.
    """
    if status not in RUNTIME_STATUSES:
        raise InvalidRuntimeStatusError(status)

    # Seed platforms so a bot heartbeat works even before the first admin save.
    system_user = bootstrap_service.ensure_system_user(session)
    bootstrap_service.ensure_bot_platforms(session, system_user.id)

    config = _ensure_config(session, platform_code, created_by=updated_by)
    config.runtime_status = status
    config.last_heartbeat = datetime.datetime.now(datetime.timezone.utc)
    config.updated_by = updated_by
    session.flush()
    return config


def get_status(
    session: Session,
    platform_code: str,
    *,
    now: datetime.datetime | None = None,
) -> str:
    """Return the effective status token for a platform.

    One of:
        ``NOT_CONFIGURED`` -- no token stored.
        ``DISABLED``       -- token stored but the platform is disabled
                              (admin turned it off).
        ``STOPPED``        -- enabled but with a stale/missing heartbeat
                              (bot process down).
        ``RUNNING``        -- enabled and the bot process heartbeated
                              recently.
        ``ERROR``          -- the bot process reported an error.
    """
    config = get_config(session, platform_code)
    if config is None or config.token_ciphertext is None:
        return "NOT_CONFIGURED"
    if not config.enabled:
        return "DISABLED"
    if config.runtime_status == "ERROR":
        return "ERROR"
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if config.last_heartbeat is not None and (
        now - config.last_heartbeat <= HEARTBEAT_STALE_AFTER
    ):
        return "RUNNING"
    return "STOPPED"


def test_connection(platform_code: str, token: str) -> tuple[bool, str, dict | None]:
    """Validate ``token`` against the platform's ``getMe`` API.

    Returns ``(ok, detail, identity)`` where ``identity`` is a dict with
    ``bot_id`` / ``username`` / ``name`` keys (from the real ``getMe``
    response) on success and ``None`` otherwise -- never raises on
    network/API errors.  The token itself is never echoed back and never
    reaches the logs (it necessarily appears inside the getMe URL, so any
    httpx request log record containing it is filtered out for the duration
    of the call).
    """
    base = _API_BASES.get(platform_code)
    if base is None:
        return False, f"Unsupported platform '{platform_code}'.", None
    if not token:
        return False, "No token provided.", None

    import httpx

    # The token is part of the getMe URL (Telegram/Bale API convention), so
    # httpx's INFO request logging would otherwise write it verbatim into
    # the application log.  Drop only the records that contain the secret;
    # unrelated httpx logging keeps flowing.
    token_filter = _TokenInLogFilter(token)
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.addFilter(token_filter)
    try:
        response = httpx.get(
            f"{base}/bot{token}/getMe",
            timeout=10.0,
        )
        if response.status_code == 200 and response.json().get("ok"):
            bot_user = response.json().get("result", {}) or {}
            username = bot_user.get("username") or "?"
            identity = {
                "bot_id": str(bot_user.get("id")) if bot_user.get("id") is not None else None,
                "username": username if username != "?" else None,
                "name": bot_user.get("first_name") or None,
            }
            return True, f"Connected as @{username}", identity
        return False, f"Invalid token (HTTP {response.status_code}).", None
    except Exception as exc:  # noqa: BLE001 - network errors are expected here
        return False, f"Connection failed: {exc}", None
    finally:
        httpx_logger.removeFilter(token_filter)


class _TokenInLogFilter(logging.Filter):
    """Drop log records that contain a bot token (e.g. httpx request URLs)."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        return self._token not in record.getMessage()


__all__ = [
    "ConfigNotFoundError",
    "HEARTBEAT_STALE_AFTER",
    "InvalidRuntimeStatusError",
    "PlatformNotFoundError",
    "RUNTIME_STATUSES",
    "SUPPORTED_PLATFORMS",
    "decrypt_token",
    "encrypt_token",
    "get_config",
    "get_plain_token",
    "get_status",
    "save_config",
    "set_identity",
    "set_runtime_status",
    "test_connection",
]