"""Shared bot logic -- platform-agnostic handlers and keyboard menus.

This module contains:
1. Keyboard layouts (main menu, contact sharing button)
2. Handler functions that make HTTP calls to the backend API
3. The phone verification flow

Both Telegram and Bale bots import and use these handlers.  The only
platform-specific part is the message sending (which each bot handles
in its own entry point) and the platform identity: each entry point calls
``set_platform()`` at startup so the platform sent to the backend is
correct (Telegram sends ``telegram``, Bale sends ``bale`` -- never the
other way around).

Token storage: the per-chat JWT cache here is a **cache only**.  The
authoritative session lives in the backend's ``bot_session`` table; if the
bot process restarts, the cache is empty and the representative simply
re-shares their phone (the backend re-issues a token for the same
persistent session).
"""

from __future__ import annotations

import decimal
import logging
from typing import Any

import httpx

from bots.config import get_bot_api_base_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform identity (set by each entry point at startup)
# ---------------------------------------------------------------------------

_current_platform: str = "telegram"


def set_platform(platform: str) -> None:
    """Set the platform identity for this bot process (telegram | bale)."""
    global _current_platform
    _current_platform = platform


def get_platform() -> str:
    """Return the platform identity of this bot process."""
    return _current_platform


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


class BotApiError(RuntimeError):
    """Raised when the backend rejects a bot API call.

    ``detail`` is the backend's error message (surfaced to the user in
    Persian-friendly form by the handlers).
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def _raise_for_error(response: httpx.Response) -> None:
    """Raise ``BotApiError`` (with the backend's JSON detail) on non-2xx."""
    if response.status_code < 400:
        return
    detail = f"HTTP {response.status_code}"
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("detail"):
            detail = str(body["detail"])
    except Exception:  # noqa: BLE001 - non-JSON error body
        pass
    raise BotApiError(response.status_code, detail)


# ---------------------------------------------------------------------------
# Keyboard layouts
# ---------------------------------------------------------------------------

MAIN_MENU_KEYBOARD = {
    "keyboard": [
        [{"text": "📦 موجودی انبار من"}, {"text": "📊 گزارش فروش من"}],
        [{"text": "🧾 صدور فاکتور جدید"}],
        [{"text": "🚪 خروج / قطع اتصال"}],
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
# Token cache (per chat_id) -- CACHE ONLY, never the source of truth.
# The authoritative session lives in the backend's bot_session table.
# ---------------------------------------------------------------------------

_token_store: dict[str, str] = {}

#: Cached representative_id per chat (from the verify-phone response).  The
#: backend bot endpoints require a real representative UUID in the URL path
#: ("self" is not a valid UUID and is rejected with 422), so the bot keeps
#: the representative's own id from login and uses it for every API call.
_rep_store: dict[str, str] = {}


def store_token(chat_id: str, token: str, rep_id: str | None = None) -> None:
    """Cache a bot JWT token (and optional representative id) for a chat_id."""
    _token_store[chat_id] = token
    if rep_id is not None:
        _rep_store[chat_id] = rep_id


def get_token(chat_id: str) -> str | None:
    """Retrieve the cached bot JWT token for a chat_id."""
    return _token_store.get(chat_id)


def get_rep_id(chat_id: str) -> str | None:
    """Retrieve the cached representative id for a chat_id.

    Set from the ``representative_id`` returned by ``POST /bot/verify-phone``;
    used as the ``rep_id`` path segment of the bot REST endpoints.
    """
    return _rep_store.get(chat_id)


def clear_token(chat_id: str) -> None:
    """Remove the cached token (and rep id) for a chat_id."""
    _token_store.pop(chat_id, None)
    _rep_store.pop(chat_id, None)


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
    Raises ``BotApiError`` on backend errors.
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
    await _raise_for_error(response)
    return response.json()


async def api_get_inventory(token: str, rep_id: str) -> dict[str, Any]:
    """Call GET /bot/reps/{rep_id}/inventory."""
    client = await get_api_client()
    response = await client.get(
        f"/bot/reps/{rep_id}/inventory",
        headers={"Authorization": f"Bearer {token}"},
    )
    await _raise_for_error(response)
    return response.json()


async def api_get_reports(token: str, rep_id: str) -> dict[str, Any]:
    """Call GET /bot/reps/{rep_id}/reports."""
    client = await get_api_client()
    response = await client.get(
        f"/bot/reps/{rep_id}/reports",
        headers={"Authorization": f"Bearer {token}"},
    )
    await _raise_for_error(response)
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
    await _raise_for_error(response)
    return response.json()


async def api_get_customers(token: str, rep_id: str) -> dict[str, Any]:
    """Call GET /bot/reps/{rep_id}/customers (ADR-007 scoped list)."""
    client = await get_api_client()
    response = await client.get(
        f"/bot/reps/{rep_id}/customers",
        headers={"Authorization": f"Bearer {token}"},
    )
    await _raise_for_error(response)
    return response.json()


async def api_get_products(token: str, rep_id: str) -> dict[str, Any]:
    """Call GET /bot/reps/{rep_id}/products (primary-warehouse inventory)."""
    client = await get_api_client()
    response = await client.get(
        f"/bot/reps/{rep_id}/products",
        headers={"Authorization": f"Bearer {token}"},
    )
    await _raise_for_error(response)
    return response.json()


async def api_price_preview(
    token: str,
    rep_id: str,
    customer_id: str,
    product_id: str,
) -> dict[str, Any]:
    """Call GET /bot/reps/{rep_id}/price-preview.

    The ERP resolves the selling price from the customer's price list
    (BR-P1) -- this call never accepts a price from the bot.
    """
    client = await get_api_client()
    response = await client.get(
        f"/bot/reps/{rep_id}/price-preview",
        params={"customer_id": customer_id, "product_id": product_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    await _raise_for_error(response)
    return response.json()


async def api_create_order(
    token: str,
    rep_id: str,
    customer_id: str,
    lines: list[dict[str, Any]],
) -> dict[str, Any]:
    """Call POST /bot/reps/{rep_id}/orders.

    Lines are ``{"product_id": ..., "qty_ordered": ...}`` -- unit prices are
    intentionally NOT sent: the ERP resolves them from the customer's price
    list inside ``order_service.create_order`` and the response carries the
    ERP-computed totals.
    """
    client = await get_api_client()
    response = await client.post(
        f"/bot/reps/{rep_id}/orders",
        json={
            "customer_id": customer_id,
            "order_type": "LOCAL",
            "fulfillment_mode": "REP_LOCAL",
            "lines": lines,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    await _raise_for_error(response)
    return response.json()


async def api_logout(token: str) -> None:
    """Call POST /bot/logout to revoke the persistent session.

    The backend rejects the token afterwards (the session is REVOKED), so
    the cache is cleared locally regardless of the outcome.
    """
    client = await get_api_client()
    response = await client.post(
        "/bot/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    await _raise_for_error(response)


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


# ---------------------------------------------------------------------------
# Order-creation flow helpers
# ---------------------------------------------------------------------------

#: ERP order states -> Persian labels used in bot messages.
ORDER_STATE_FA = {
    "DRAFT": "پیش‌نویس",
    "PENDING_APPROVAL": "در انتظار تأیید",
    "APPROVED": "تأیید شده",
    "RESERVED": "رزرو شده",
    "FULFILLING": "در حال اجرا",
    "SHIPPED": "ارسال شده",
    "INVOICED": "فاکتور شده",
    "PAID": "پرداخت شده",
    "COMPLETED": "تکمیل شده",
    "CANCELLED": "لغو شده",
    "BACKORDERED": "سفارش معوق",
    "PARTIALLY_FULFILLED": "اجرای جزئی",
    "RETURNED": "برگشتی",
}


def order_state_fa(state: str) -> str:
    """Map an ERP order state code to its Persian label (fallback: raw code)."""
    return ORDER_STATE_FA.get(state, state)


def parse_quantity(text: str) -> decimal.Decimal | None:
    """Parse a Telegram-entered quantity into a positive Decimal.

    Accepts ``10``, ``2.5`` and thousands-separated ``1,000`` / ``۱٬۰۰۰``.
    Returns ``None`` for empty/non-numeric/<= 0 input so callers can show
    a Persian validation message.
    """
    import decimal

    cleaned = text.strip().replace(",", "").replace("٬", "")
    if not cleaned:
        return None
    try:
        qty = decimal.Decimal(cleaned)
    except decimal.InvalidOperation:
        return None
    if not qty.is_finite() or qty <= 0:
        return None
    return qty


def format_draft_review(
    customer_name: str,
    lines: list[dict[str, Any]],
    balances: dict[str, int] | None = None,
) -> str:
    """Format the multi-line order draft for confirmation.

    ``lines`` items: ``{product_id, sku, name, qty, unit_price, line_total}``.
    When ``balances`` is provided, lines whose qty exceeds the available
    balance are flagged with a warning (no hard block -- the ERP decides).
    """
    parts = ["🧾 پیش‌نویس سفارش", f"مشتری: {customer_name}", "─" * 30]
    total = decimal.Decimal("0")
    for idx, ln in enumerate(lines, start=1):
        qty = decimal.Decimal(str(ln["qty"]))
        line_total = decimal.Decimal(str(ln["line_total"]))
        total += line_total
        parts.append(f"{idx}. {ln['sku']} {ln['name']}")
        parts.append(f"   تعداد: {qty:g} | قیمت واحد: {ln['unit_price']:,.0f}")
        parts.append(f"   مبلغ: {line_total:,.0f}")
        if balances and ln["product_id"] in balances:
            available = balances[ln["product_id"]]
            if qty > available:
                parts.append(
                    f"   ⚠️ تعداد بیش از موجودی انبار است (موجودی: {available})"
                )
    parts.append("─" * 30)
    parts.append(f"جمع کل: {total:,.0f}")
    return "\n".join(parts)


def format_order_created(data: dict[str, Any]) -> str:
    """Format the order-creation confirmation.

    Deliberately says an ORDER was registered (not an invoice): the ERP
    lifecycle creates a DRAFT order; invoicing happens later through the
    ERP after approval/shipment.
    """
    return (
        "✅ سفارش با موفقیت ثبت شد.\n"
        f"شماره سفارش: {data['order_number']}\n"
        f"مبلغ کل: {data['grand_total']:,.0f}\n"
        f"وضعیت: {order_state_fa(data['state'])}"
    )


__all__ = [
    "BotApiError",
    "api_create_invoice",
    "api_create_order",
    "api_get_customers",
    "api_get_inventory",
    "api_get_products",
    "api_get_reports",
    "api_logout",
    "api_price_preview",
    "api_verify_phone",
    "clear_token",
    "close_api_client",
    "format_draft_review",
    "format_inventory_text",
    "format_invoice_text",
    "format_order_created",
    "format_reports_text",
    "get_contact_button_markup",
    "get_main_menu_markup",
    "get_platform",
    "get_rep_id",
    "get_token",
    "order_state_fa",
    "parse_quantity",
    "set_platform",
    "store_token",
]