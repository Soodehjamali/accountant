"""Telegram bot adapter -- polling architecture.

This is the **only module** that makes HTTP calls to the Telegram Bot API.
It owns:
    - The long-polling loop (``run_polling``).
    - Outbound message sending (``send_message``).
    - The lifecycle (start / stop).

**Zero business logic.**  All command processing is delegated to a
``process_message`` callback injected at construction time.  This
adapter only handles Telegram-specific I/O.

Architecture note:
    This adapter is designed for Phase A (polling).  A future webhook
    implementation would replace the polling loop with a FastAPI endpoint
    but reuse the same ``send_message`` / ``format_response`` helpers.

Environment variables:
    ``TELEGRAM_BOT_TOKEN`` -- required.  The bot token from @BotFather.
    Never logged or included in error messages.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

import httpx

from services.bot_command_service import BotMessage, BotResponse
from telegram_adapter.formatter import format_response
from telegram_adapter.normalizer import normalize_update

logger = logging.getLogger(__name__)

#: Telegram Bot API base URL.
_TELEGRAM_API_BASE = "https://api.telegram.org"

#: Polling timeout in seconds (long-polling).
_POLL_TIMEOUT = 30

#: Delay between polls when no updates are available.
_POLL_BACKOFF = 1


class TelegramAdapterError(Exception):
    """Base exception for Telegram adapter errors."""


class TelegramAPIError(TelegramAdapterError):
    """Raised when a Telegram API call fails."""

    def __init__(self, method: str, status_code: int, description: str) -> None:
        super().__init__(
            f"Telegram API error on {method}: {status_code} - {description}"
        )
        self.method = method
        self.status_code = status_code
        self.description = description


class TelegramBot:
    """Telegram bot with long-polling architecture.

    Usage::

        bot = TelegramBot(process_message=my_process_fn)
        bot.run_polling()

    The ``process_message`` callback must accept a ``BotMessage`` and
    return a ``BotResponse``.  It is called synchronously for each
    incoming update.

    The bot token is read from the ``TELEGRAM_BOT_TOKEN`` environment
    variable at construction time.  It is never stored in instance
    attributes or logged.
    """

    def __init__(
        self,
        *,
        process_message: Callable[[BotMessage], BotResponse],
        token: str | None = None,
    ) -> None:
        """Initialize the Telegram bot.

        Args:
            process_message: callback that processes a ``BotMessage`` and
                returns a ``BotResponse``.
            token: Telegram bot token.  If ``None``, reads from
                ``TELEGRAM_BOT_TOKEN`` env var.
        """
        self._process_message = process_message
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self._token:
            raise TelegramAdapterError(
                "TELEGRAM_BOT_TOKEN is not set. "
                "Configure it via environment variable or .env file."
            )
        self._offset: int = 0
        self._running: bool = False
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        """Return the HTTP client, creating it lazily."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=f"{_TELEGRAM_API_BASE}/bot{self._token}",
                timeout=httpx.Timeout(_POLL_TIMEOUT + 10),
            )
        return self._client

    def _api_call(self, method: str, **params: Any) -> dict[str, Any]:
        """Make a Telegram Bot API call.

        Never logs the token or full URL.  Only logs the method name.
        """
        client = self._get_client()
        try:
            response = client.post(f"/{method}", json=params)
            data = response.json()
        except httpx.HTTPError as exc:
            raise TelegramAdapterError(
                f"HTTP error calling Telegram API method '{method}': {exc}"
            ) from exc

        if not data.get("ok", False):
            raise TelegramAPIError(
                method=method,
                status_code=response.status_code,
                description=data.get("description", "Unknown error"),
            )

        return data.get("result", {})

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a message via the Telegram Bot API.

        Never logs the message content at INFO level (it may contain
        sensitive business data).  DEBUG-level logging is available.
        """
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id

        logger.debug("Sending message to chat %s", chat_id)
        return self._api_call("sendMessage", **params)

    def _process_update(self, update: dict[str, Any]) -> None:
        """Process a single Telegram Update.

        1. Normalize the update to a ``BotMessage``.
        2. Call the ``process_message`` callback.
        3. Send the response back via Telegram.
        """
        bot_message = normalize_update(update)
        if bot_message is None:
            return

        try:
            response = self._process_message(bot_message)
        except Exception as exc:
            logger.exception("Error processing bot message")
            response = BotResponse(text=f"An error occurred: {exc}")

        # Determine the chat_id for the reply.
        chat_id = bot_message.metadata.get("telegram_chat_id", "")
        if not chat_id:
            chat_id = bot_message.platform_user_id

        # Format and send.
        telegram_params = format_response(response, chat_id=chat_id)
        try:
            self._api_call("sendMessage", **telegram_params)
        except TelegramAPIError as exc:
            logger.error("Failed to send response: %s", exc)

    def poll_once(self) -> bool:
        """Poll for updates once.  Returns ``True`` if updates were received."""
        try:
            updates = self._api_call(
                "getUpdates",
                offset=self._offset,
                timeout=_POLL_TIMEOUT,
            )
        except TelegramAPIError as exc:
            logger.error("Polling error: %s", exc)
            return False

        if not updates:
            return False

        for update in updates:
            self._process_update(update)
            self._offset = update["update_id"] + 1

        return True

    def run_polling(self) -> None:
        """Start the long-polling loop.

        Blocks until ``stop()`` is called.  Handles ``KeyboardInterrupt``
        for graceful shutdown.
        """
        self._running = True
        logger.info("Telegram bot polling started")

        try:
            while self._running:
                try:
                    had_updates = self.poll_once()
                    if not had_updates:
                        time.sleep(_POLL_BACKOFF)
                except KeyboardInterrupt:
                    break
        finally:
            self._running = False
            self.close()
            logger.info("Telegram bot polling stopped")

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._running = False

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            self._client.close()


def create_bot(process_message: Callable[[BotMessage], BotResponse]) -> TelegramBot:
    """Factory function to create a TelegramBot from environment config."""
    return TelegramBot(process_message=process_message)


__all__ = ["TelegramAPIError", "TelegramAdapterError", "TelegramBot", "create_bot"]
