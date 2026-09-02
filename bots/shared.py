"""Shared bot logic — platform-agnostic handlers and keyboard menus.

This module contains:
1. Keyboard layouts (main menu, contact sharing button)
2. Handler functions that make HTTP calls to the backend API
3. The phone verification flow

Both Telegram and Bale bots import and use these handlers.  The only
platform-specific part is the message sending (which each bot handles
in its own entry point).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from bots.config import get_bot_api_base_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API client (shared)
# ---------------------------------------------------------------------------

_api_client: httpx.AsyncClient | None = None


async def get_api_client() -> httpx.AsyncClient:
    """Return the shared async HTTP client for backend API calls."""
    global _api_client
    if _api_client is None or _api_client.is_closed:
        _api_client = httpx.AsyncClient(
            base_url=get_bot_api_base_url(),
            timeout=30.0,
        )
    return _api_client


async def close_api_client() -> None:
    """Close the shared HTTP client on shutdown."""
    global _api_client
    if _api_client is not None and not _api_client.is_closed:
        await _api_client.close()
        _api_client = None


# ---------------------------------------------------------------------------
# Keyboard layouts
# ---------------------------------------------------------------------------

MAIN_MENU_KEYBOARD = {
    "keyboard": [
        [{"text": "📦 موجودی انبار من"}, {"text": "📊 گزارش فروش من"}],
        [{"text": "🧾 صدور فاکتور جدید"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

CONTACT_BUTTON = {
    "keyboard": [
        [{"text": "📱 اشتراک‌گذاری شماره موبایل", "request_contact": True}],
    ],
    "resize_keyboard": True,
}


def get_main_menu_markup() -> dict[str, Any]:
    """Return the main menu keyboard as a JSON-serializable dict."""
    return MAIN_MENU_KEYBOARD


def get_contact_button_markup() -> dict[str, Any]:
    """Return the contact-sharing keyboard as a JSON-serializable dict."""
    return CONTACT_BUTTON


# ---------------------------------------------------------------------------
# Token storage (in-memory, per chat_id)
# ---------------------------------------------------------------------------

# In production, store tokens in a database or encrypted file.
# For now, a simple in-memory dict keyed by chat_id.
_token_store: dict[str, str] = {}


def store_token(chat_id: str, token: str) -> None:
    """Store a bot JWT token for a chat_id."""
    _token_store[chat_id] = token


def get_token(chat_id: str) -> str | None:
    """Retrieve the bot JWT token for a chat_id."""
    return _token_store.get(chat_id)


def clear_token(chat_id: str) -> None:
    """Remove the stored token for a chat_id."""
    _token_store.pop(chat_id, None)


# ---------------------------------------------------------------------------
# API call helpers
# ---------------------------------------------------------------------------


async def api_verify_phone(
    phone_number: str,
    platform: str,
    chat_id: str,
) -> dict[str, Any]:
    """Call POST /bot/verify-phone and return the response.

    Returns the parsed JSON response on success.
    Raises on HTTP errors.
    """
    client = await get_api_client()
    response = await client.post(
        "/bot/verify-phone",
        json={
            "phone_number": phone_number,
            "platform": platform,
            "chat_id": chat_id,
        },
    )
    response.raise_for_status()
    return response.json()


async def api_get_inventory(token: str, rep_id: str) -> dict[str, Any]:
    """Call GET /bot/reps/{rep_id}/inventory."""
    client = await get_api_client()
    response = await client.get(
        f"/bot/reps/{rep_id}/inventory",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json()


async def api_get_reports(token: str, rep_id: str) -> dict[str, Any]:
    """Call GET /bot/reps/{rep_id}/reports."""
    client = await get_api_client()
    response = await client.get(
        f"/bot/reps/{rep_id}/reports",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json()


async def api_create_invoice(
    token: str,
    rep_id: str,
    order_number: str,
) -> dict[str, Any]:
    """Call POST /bot/reps/{rep_id}/invoices."""
    client = await get_api_client()
    response = await client.post(
        f"/bot/reps/{rep_id}/invoices",
        json={"order_number": order_number},
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_inventory_text(data: dict[str, Any]) -> str:
    """Format inventory response as Persian text."""
    warehouse = data.get("warehouse_code", "N/A")
    items = data.get("items", [])

    if not items:
        return f"📦 انبار {warehouse}: موجودی خالی است."

    lines = [f"📦 موجودی انبار {warehouse}:"]
    lines.append("─" * 30)
    for item in items:
        lines.append(f"  {item['sku']}")
        lines.append(f"  {item['name']}: {item['balance']}")
        lines.append("")

    return "\n".join(lines)


def format_reports_text(data: dict[str, Any]) -> str:
    """Format reports response as Persian text."""
    name = data.get("representative_name", "")
    period = data.get("period", "")
    summaries = data.get("summaries", [])

    lines = [f"📊 گزارش فروش — {name}"]
    lines.append(f"📅 دوره: {period}")
    lines.append("─" * 30)
    for s in summaries:
        lines.append(f"  {s['label']}: {s['value']}")

    return "\n".join(lines)


def format_invoice_text(data: dict[str, Any]) -> str:
    """Format invoice creation response as Persian text."""
    return (
        f"🧾 فاکتور صادر شد!\n"
        f"  شماره فاکتور: {data['invoice_number']}\n"
        f"  شماره سفارش: {data['order_number']}\n"
        f"  مبلغ کل: {data['grand_total']:,.0f}\n"
        f"  وضعیت: {data['status']}"
    )


__all__ = [
    "api_create_invoice",
    "api_get_inventory",
    "api_get_reports",
    "api_verify_phone",
    "clear_token",
    "close_api_client",
    "format_inventory_text",
    "format_invoice_text",
    "format_reports_text",
    "get_contact_button_markup",
    "get_main_menu_markup",
    "get_token",
    "store_token",
]
