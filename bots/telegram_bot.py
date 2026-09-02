"""Telegram bot entry point using aiogram 3.

Run with::

    python -m bots.telegram_bot

Or::

    TELEGRAM_BOT_TOKEN=your_token python -m bots.telegram_bot

This bot:
1. Shows a "Share Phone" button on /start
2. Verifies the phone against the backend
3. Shows a main menu with keyboard buttons
4. Maps button taps to backend API calls
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from bots.config import get_telegram_token
from bots.shared import (
    api_create_invoice,
    api_get_inventory,
    api_get_reports,
    api_verify_phone,
    clear_token,
    close_api_client,
    format_inventory_text,
    format_invoice_text,
    format_reports_text,
    get_token,
    store_token,
)

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

router = Router()

PLATFORM = "telegram"


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def _contact_keyboard() -> ReplyKeyboardMarkup:
    """Build a keyboard with a 'Share Phone' button."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 اشتراک‌گذاری شماره موبایل", request_contact=True)]],
        resize_keyboard=True,
    )


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Build the main menu keyboard."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📦 موجودی انبار من"),
                KeyboardButton(text="📊 گزارش فروش من"),
            ],
            [KeyboardButton(text="🧾 صدور فاکتور جدید")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Handle /start — show the contact sharing button."""
    await message.answer(
        "سلام! 👋\n\n"
        "برای استفاده از ربات، لطفاً شماره موبایل خود را به اشتراک بگذارید.\n"
        "دکمه زیر را فشار دهید:",
        reply_markup=_contact_keyboard(),
    )


@router.message(F.contact)
async def handle_contact(message: Message) -> None:
    """Handle shared contact — verify phone against backend."""
    if message.contact is None or message.contact.phone_number is None:
        await message.answer("❌ اطلاعات تماس دریافت نشد. لطفاً دوباره تلاش کنید.")
        return

    phone = message.contact.phone_number
    chat_id = str(message.chat.id)

    await message.answer("⏳ در حال بررسی شماره موبایل...")

    try:
        result = await api_verify_phone(
            phone_number=phone,
            platform=PLATFORM,
            chat_id=chat_id,
        )
    except Exception as exc:
        logger.exception("Phone verification failed")
        await message.answer(
            "❌ خطا در احراز هویت.\n"
            f"لطفاً با مدیر سیستم تماس بگیرید.\n"
            f"خطا: {exc}",
            reply_markup=_contact_keyboard(),
        )
        return

    # Store the token for subsequent API calls.
    store_token(chat_id, result["access_token"])

    await message.answer(
        f"✅ احراز هویت موفق!\n"
        f"خوش آمدید، {result['representative_name']}!\n\n"
        "از منوی زیر استفاده کنید:",
        reply_markup=_main_menu_keyboard(),
    )


@router.message(F.text == "📦 موجودی انبار من")
async def handle_inventory(message: Message) -> None:
    """Handle 'Inventory' button tap."""
    chat_id = str(message.chat.id)
    token = get_token(chat_id)

    if token is None:
        await message.answer(
            "❌ لطفاً ابتدا شماره موبایل خود را به اشتراک بگذارید.",
            reply_markup=_contact_keyboard(),
        )
        return

    await message.answer("⏳ در حال دریافت موجودی انبار...")

    try:
        # We need rep_id — extract from token or make a /me call.
        # For simplicity, the backend will extract rep_id from the token.
        # We'll call the inventory endpoint with a dummy rep_id that the
        # backend ignores (it extracts from JWT).
        result = await api_get_inventory(token, "self")
        text = format_inventory_text(result)
        await message.answer(text, reply_markup=_main_menu_keyboard())
    except Exception as exc:
        logger.exception("Inventory fetch failed")
        await message.answer(
            f"❌ خطا در دریافت موجودی: {exc}",
            reply_markup=_main_menu_keyboard(),
        )


@router.message(F.text == "📊 گزارش فروش من")
async def handle_reports(message: Message) -> None:
    """Handle 'Reports' button tap."""
    chat_id = str(message.chat.id)
    token = get_token(chat_id)

    if token is None:
        await message.answer(
            "❌ لطفاً ابتدا شماره موبایل خود را به اشتراک بگذارید.",
            reply_markup=_contact_keyboard(),
        )
        return

    await message.answer("⏳ در حال دریافت گزارش فروش...")

    try:
        result = await api_get_reports(token, "self")
        text = format_reports_text(result)
        await message.answer(text, reply_markup=_main_menu_keyboard())
    except Exception as exc:
        logger.exception("Reports fetch failed")
        await message.answer(
            f"❌ خطا در دریافت گزارش: {exc}",
            reply_markup=_main_menu_keyboard(),
        )


@router.message(F.text == "🧾 صدور فاکتور جدید")
async def handle_create_invoice_start(message: Message) -> None:
    """Handle 'Create Invoice' button tap — ask for order number."""
    chat_id = str(message.chat.id)
    token = get_token(chat_id)

    if token is None:
        await message.answer(
            "❌ لطفاً ابتدا شماره موبایل خود را به اشتراک بگذارید.",
            reply_markup=_contact_keyboard(),
        )
        return

    await message.answer(
        "لطفاً شماره سفارش را وارد کنید:\n"
        "(مثال: ORD-2026-0001)"
    )


@router.message(F.text.startswith("ORD-"))
async def handle_invoice_order_number(message: Message) -> None:
    """Handle order number input for invoice creation."""
    chat_id = str(message.chat.id)
    token = get_token(chat_id)
    order_number = message.text.strip()

    if token is None:
        await message.answer(
            "❌ لطفاً ابتدا شماره موبایل خود را به اشتراک بگذارید.",
            reply_markup=_contact_keyboard(),
        )
        return

    await message.answer(f"⏳ در حال صدور فاکتور برای سفارش {order_number}...")

    try:
        result = await api_create_invoice(token, "self", order_number)
        text = format_invoice_text(result)
        await message.answer(text, reply_markup=_main_menu_keyboard())
    except Exception as exc:
        logger.exception("Invoice creation failed")
        await message.answer(
            f"❌ خطا در صدور فاکتور: {exc}",
            reply_markup=_main_menu_keyboard(),
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    """Start the Telegram bot."""
    token = get_telegram_token()
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Telegram bot starting...")

    try:
        await dp.start_polling(bot)
    finally:
        await close_api_client()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
