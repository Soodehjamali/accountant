"""Telegram bot entry point using aiogram 3.

Run with::

    python -m bots.telegram_bot

Or::

    TELEGRAM_BOT_TOKEN=your_token python -m bots.telegram_bot

This bot:
1. Shows a "Share Phone" button on /start
2. Verifies the phone against the backend (platform identity = ``telegram``)
3. Shows a main menu with keyboard buttons
4. Maps button taps to backend API calls

The ``🧾 صدور فاکتور جدید`` menu button starts a real multi-step order
conversation (select customer -> select product -> quantity -> review ->
confirm).  The backend remains the source of truth: prices are resolved by
the ERP (BR-P1 customer price list) and the order is created through
``order_service.create_order`` as a DRAFT -- the bot never sends a price and
never creates an invoice directly (invoicing happens later in the ERP after
approval/shipment).

Authentication/session state lives on the backend (persistent ``bot_session``
table) -- this process only caches the short-lived JWT (and the
representative id) per chat.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from bots.config import get_telegram_proxy, get_telegram_token
from bots.shared import (
    BotApiError,
    api_create_order,
    api_get_customers,
    api_get_inventory,
    api_get_products,
    api_get_reports,
    api_logout,
    api_price_preview,
    api_verify_phone,
    clear_token,
    close_api_client,
    format_draft_review,
    format_inventory_text,
    format_order_created,
    format_reports_text,
    get_platform,
    get_rep_id,
    get_token,
    parse_quantity,
    set_platform,
    store_token,
)

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

router = Router()

#: Platform identity reported to the backend (must be "telegram").
PLATFORM = "telegram"

#: Number of customers/products per inline-keyboard page.
PAGE_SIZE = 8


# ---------------------------------------------------------------------------
# Order conversation state (scoped per chat by the Dispatcher's FSM storage)
# ---------------------------------------------------------------------------


class OrderFlow(StatesGroup):
    """Multi-step order-creation conversation states.

    State data (per chat) never leaks between representatives because the
    FSM key is the Telegram (chat, user) pair.
    """

    selecting_customer = State()
    selecting_product = State()
    entering_quantity = State()
    add_more_or_confirm = State()


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
            [KeyboardButton(text="🚪 خروج / قطع اتصال")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def _customer_button_text(c: dict) -> str:
    return f"🏢 {c['code']} — {c['name']}"


def _product_button_text(p: dict) -> str:
    return f"📦 {p['sku']} — {p['name']} ({p['balance']})"


def _paged_keyboard(
    items: list[dict],
    *,
    kind: str,
    page: int,
    label_fn,
    page_size: int = PAGE_SIZE,
) -> InlineKeyboardMarkup:
    """Build a paged inline keyboard for customers (``cust``) or products (``prod``).

    Callbacks: ``{kind}:{id}`` for selection, ``page:{kind}:{offset}`` for
    paging, and a trailing ``action:cancel`` row.
    """
    rows: list[list[InlineKeyboardButton]] = []
    start = page * page_size
    chunk = items[start : start + page_size]
    for item in chunk:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label_fn(item),
                    callback_data=f"{kind}:{item['id']}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️ قبلی",
                callback_data=f"page:{kind}:{page - 1}",
            )
        )
    if start + page_size < len(items):
        nav.append(
            InlineKeyboardButton(
                text="بعدی ➡️",
                callback_data=f"page:{kind}:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="❌ لغو سفارش", callback_data="action:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _review_keyboard() -> InlineKeyboardMarkup:
    """Review-time actions: add another product, confirm, or cancel."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزودن محصول", callback_data="action:add")],
            [InlineKeyboardButton(text="✅ تأیید و ثبت سفارش", callback_data="action:confirm")],
            [InlineKeyboardButton(text="❌ لغو", callback_data="action:cancel")],
        ]
    )


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _auth_required(message: Message) -> bool:
    """Return False (and prompt re-auth) when the chat has no cached token."""
    if get_token(str(message.chat.id)) is None:
        asyncio.get_running_loop().create_task(
            message.answer(
                "❌ لطفاً ابتدا شماره موبایل خود را به اشتراک بگذارید.",
                reply_markup=_contact_keyboard(),
            )
        )
        return False
    return True


def _rep_id_for(chat_id: str) -> str:
    """Return the representative id cached at login (URL path segment).

    The backend bot endpoints require a real representative UUID -- "self"
    is not a valid UUID.  ``get_rep_id`` is set together with the token at
    phone verification, so it is always present when a token is present;
    the fallback exists only for chats authenticated before this cache
    existed (a process restart clears both anyway).
    """
    return get_rep_id(chat_id) or "self"


async def _reauth(message: Message, state: FSMContext) -> None:
    """Session expired/revoked: clear local state and ask for phone again."""
    chat_id = str(message.chat.id)
    clear_token(chat_id)
    await state.clear()
    await message.answer(
        "❌ نشست شما منقضی شده است. لطفاً دوباره شماره موبایل خود را "
        "به اشتراک بگذارید.",
        reply_markup=_contact_keyboard(),
    )


async def _flow_backend_error(message: Message, state: FSMContext, action: str) -> None:
    """Backend unavailable during the order flow: friendly Persian message."""
    logger.exception("%s failed", action)
    await state.clear()
    await message.answer(
        "❌ خطا در ارتباط با سرور. لطفاً چند لحظه بعد دوباره تلاش کنید.",
        reply_markup=_main_menu_keyboard(),
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
            platform=get_platform(),
            chat_id=chat_id,
        )
    except BotApiError as exc:
        logger.warning("Phone verification failed: %s", exc.detail)
        await message.answer(
            "❌ شماره موبایل تأیید نشد.\n"
            "اگر نماینده فعال این سیستم هستید و شماره شما ثبت شده، "
            "لطفاً با مدیر سیستم تماس بگیرید.",
            reply_markup=_contact_keyboard(),
        )
        return
    except Exception:
        logger.exception("Phone verification failed (unexpected)")
        await message.answer(
            "❌ خطا در اتصال به سرور. لطفاً چند لحظه بعد دوباره تلاش کنید.",
            reply_markup=_contact_keyboard(),
        )
        return

    # Cache the JWT + representative id for subsequent API calls (the
    # backend owns the real session and the rep id is taken from the
    # verified login, never from a user-supplied value).
    store_token(
        chat_id,
        result["access_token"],
        result.get("representative_id"),
    )

    await message.answer(
        f"✅ احراز هویت موفق!\n"
        f"خوش آمدید، {result['representative_name']}!\n\n"
        "از منوی زیر استفاده کنید:",
        reply_markup=_main_menu_keyboard(),
    )


@router.message(F.text == "📦 موجودی انبار من")
async def handle_inventory(message: Message, state: FSMContext) -> None:
    """Handle 'Inventory' button tap (also aborts any in-progress order)."""
    chat_id = str(message.chat.id)
    if not _auth_required(message):
        return
    await state.clear()

    token = get_token(chat_id)
    await message.answer("⏳ در حال دریافت موجودی انبار...")

    try:
        result = await api_get_inventory(token, _rep_id_for(chat_id))
        text = format_inventory_text(result)
        await message.answer(text, reply_markup=_main_menu_keyboard())
    except BotApiError as exc:
        await _handle_auth_error(message, exc, "دریافت موجودی")
    except Exception as exc:
        logger.exception("Inventory fetch failed")
        await message.answer(
            f"❌ خطا در دریافت موجودی: {exc}",
            reply_markup=_main_menu_keyboard(),
        )


@router.message(F.text == "📊 گزارش فروش من")
async def handle_reports(message: Message, state: FSMContext) -> None:
    """Handle 'Reports' button tap (also aborts any in-progress order)."""
    chat_id = str(message.chat.id)
    if not _auth_required(message):
        return
    await state.clear()

    token = get_token(chat_id)
    await message.answer("⏳ در حال دریافت گزارش فروش...")

    try:
        result = await api_get_reports(token, _rep_id_for(chat_id))
        text = format_reports_text(result)
        await message.answer(text, reply_markup=_main_menu_keyboard())
    except BotApiError as exc:
        await _handle_auth_error(message, exc, "دریافت گزارش")
    except Exception as exc:
        logger.exception("Reports fetch failed")
        await message.answer(
            f"❌ خطا در دریافت گزارش: {exc}",
            reply_markup=_main_menu_keyboard(),
        )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Handle /cancel — abort any in-progress order flow."""
    await state.clear()
    await message.answer(
        "❌ عملیات لغو شد.",
        reply_markup=_main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# Order-creation flow (🧾 صدور فاکتور جدید)
# ---------------------------------------------------------------------------


@router.message(F.text == "🧾 صدور فاکتور جدید")
async def handle_create_invoice_start(message: Message, state: FSMContext) -> None:
    """Start the real order-creation conversation.

    The menu label is kept as-is (``صدور فاکتور جدید``) but the action
    creates an ORDER through the ERP lifecycle -- invoicing still happens
    later in the ERP after approval/shipment.
    """
    chat_id = str(message.chat.id)
    if not _auth_required(message):
        return

    # Restart the flow from scratch (also covers re-tapping mid-flow).
    await state.clear()

    token = get_token(chat_id)
    rep_id = _rep_id_for(chat_id)

    try:
        data = await api_get_customers(token, rep_id)
    except BotApiError as exc:
        if exc.status_code in (401, 403):
            await _reauth(message, state)
        else:
            await state.clear()
            await message.answer(
                "❌ خطا در دریافت مشتریان. لطفاً چند لحظه بعد دوباره تلاش کنید.",
                reply_markup=_main_menu_keyboard(),
            )
        return
    except Exception:
        await _flow_backend_error(message, state, "Customer fetch")
        return

    customers = data.get("items", [])
    if not customers:
        await state.clear()
        await message.answer(
            "❌ هیچ مشتری‌ای به شما اختصاص داده نشده است.\n"
            "برای ثبت سفارش، ابتدا در ERP مشتری به شما اختصاص داده شود.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    await state.update_data(customers=customers, customer_page=0)
    await message.answer(
        "🧾 ثبت سفارش جدید\n"
        "لطفاً مشتری را انتخاب کنید:\n\n"
        "توجه: این عملیات در ERP یک سفارش (پیش‌نویس) ایجاد می‌کند؛ "
        "صدور فاکتور پس از تأیید و ارسال در ERP انجام می‌شود.",
        reply_markup=_paged_keyboard(
            customers, kind="cust", page=0, label_fn=_customer_button_text
        ),
    )
    await state.set_state(OrderFlow.selecting_customer)


@router.callback_query(OrderFlow.selecting_customer, F.data.startswith("cust:"))
async def cb_select_customer(callback: CallbackQuery, state: FSMContext) -> None:
    """Customer chosen from the inline list -> show the product list."""
    await callback.answer()
    data = await state.get_data()

    customer_id = callback.data.split(":", 1)[1]
    customer = next(
        (c for c in data.get("customers", []) if c["id"] == customer_id), None
    )
    if customer is None:
        await callback.message.answer(
            "❌ مشتری یافت نشد. لطفاً دوباره از فهرست انتخاب کنید."
        )
        return

    chat_id = str(callback.message.chat.id)
    token = get_token(chat_id)
    rep_id = _rep_id_for(chat_id)

    try:
        products_data = await api_get_products(token, rep_id)
    except BotApiError as exc:
        if exc.status_code in (401, 403):
            await _reauth(callback.message, state)
        else:
            await state.clear()
            await callback.message.answer(
                "❌ خطا در دریافت محصولات. لطفاً چند لحظه بعد دوباره تلاش کنید.",
                reply_markup=_main_menu_keyboard(),
            )
        return
    except Exception:
        await _flow_backend_error(callback.message, state, "Products fetch")
        return

    products = [
        {
            "id": p["product_id"],
            "sku": p["sku"],
            "name": p["name"],
            "balance": int(p["balance"]),
        }
        for p in products_data.get("items", [])
    ]
    if not products:
        await state.clear()
        await callback.message.answer(
            "❌ در انبار شما محصولی برای فروش موجود نیست.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    await state.update_data(
        customer_id=customer_id,
        customer_name=customer["name"],
        products=products,
        product_page=0,
        pending_product_id=None,
    )
    await callback.message.answer(
        f"مشتری: {customer['name']}\n\nلطفاً محصول را انتخاب کنید:",
        reply_markup=_paged_keyboard(
            products, kind="prod", page=0, label_fn=_product_button_text
        ),
    )
    await state.set_state(OrderFlow.selecting_product)


@router.callback_query(OrderFlow.selecting_product, F.data.startswith("prod:"))
async def cb_select_product(callback: CallbackQuery, state: FSMContext) -> None:
    """Product chosen -> ask for quantity (balance shown as a hint)."""
    await callback.answer()
    data = await state.get_data()

    product_id = callback.data.split(":", 1)[1]
    product = next(
        (p for p in data.get("products", []) if p["id"] == product_id), None
    )
    if product is None:
        await callback.message.answer(
            "❌ محصول یافت نشد. لطفاً دوباره از فهرست انتخاب کنید."
        )
        return

    await state.update_data(pending_product_id=product_id)
    await callback.message.answer(
        f"📦 {product['sku']} — {product['name']}\n"
        f"موجودی فعلی: {product['balance']}\n\n"
        "تعداد را وارد کنید (عددی بزرگ‌تر از صفر، مثلاً 10 یا 2.5):"
    )
    await state.set_state(OrderFlow.entering_quantity)


@router.message(OrderFlow.entering_quantity, F.text)
async def msg_enter_quantity(message: Message, state: FSMContext) -> None:
    """Quantity entered -> resolve the ERP price and show the line."""
    chat_id = str(message.chat.id)
    token = get_token(chat_id)
    rep_id = _rep_id_for(chat_id)

    data = await state.get_data()
    product_id = data.get("pending_product_id")
    product = next(
        (p for p in data.get("products", []) if p["id"] == product_id), None
    ) if product_id else None
    if product is None:
        await state.clear()
        await message.answer(
            "❌ فرآیند سفارش از نو آغاز شد. لطفاً دوباره تلاش کنید.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    qty = parse_quantity(message.text or "")
    if qty is None:
        await message.answer(
            "❌ تعداد وارد شده معتبر نیست.\n"
            "لطفاً یک عدد بزرگ‌تر از صفر وارد کنید (مثلاً 10 یا 2.5):"
        )
        return

    # Price comes from the ERP (BR-P1 chain) -- never from the representative.
    try:
        preview = await api_price_preview(
            token, rep_id, data["customer_id"], product_id,
        )
    except BotApiError as exc:
        if exc.status_code in (401, 403):
            await _reauth(message, state)
            return
        # 422: no price list / no active price for this customer+product.
        await message.answer(
            "❌ برای این محصول و مشتری قیمت فروش فعالی در ERP پیدا نشد.\n"
            "لطفاً ابتدا لیست قیمت را در ERP تنظیم کنید."
        )
        # Stay in the flow: back to the product list so the rep can pick
        # another product (or cancel).
        await state.update_data(product_page=0)
        await message.answer(
            "لطفاً محصول دیگری انتخاب کنید یا لغو کنید:",
            reply_markup=_paged_keyboard(
                data.get("products", []),
                kind="prod",
                page=0,
                label_fn=_product_button_text,
            ),
        )
        await state.set_state(OrderFlow.selecting_product)
        return
    except Exception:
        logger.exception("Price preview failed")
        await message.answer(
            "❌ خطا در دریافت قیمت. لطفاً چند لحظه بعد دوباره تلاش کنید."
        )
        return

    unit_price = float(preview["unit_price"])
    qty_float = float(qty)
    line_total = unit_price * qty_float

    lines = list(data.get("lines", []))
    lines.append(
        {
            "product_id": product_id,
            "sku": preview.get("product_sku") or product["sku"],
            "name": preview.get("product_name") or product["name"],
            "qty": qty_float,
            "unit_price": unit_price,
            "line_total": line_total,
        }
    )
    await state.update_data(lines=lines, pending_product_id=None)

    balances = {p["id"]: p["balance"] for p in data.get("products", [])}
    await message.answer(
        format_draft_review(data["customer_name"], lines, balances=balances),
        reply_markup=_review_keyboard(),
    )
    await state.set_state(OrderFlow.add_more_or_confirm)


@router.callback_query(OrderFlow.add_more_or_confirm, F.data == "action:add")
async def cb_add_more(callback: CallbackQuery, state: FSMContext) -> None:
    """Add another product to the draft order."""
    await callback.answer()
    data = await state.get_data()
    await state.update_data(product_page=0, pending_product_id=None)
    await callback.message.answer(
        "لطفاً محصول را انتخاب کنید:",
        reply_markup=_paged_keyboard(
            data.get("products", []),
            kind="prod",
            page=0,
            label_fn=_product_button_text,
        ),
    )
    await state.set_state(OrderFlow.selecting_product)


@router.callback_query(OrderFlow.add_more_or_confirm, F.data == "action:confirm")
async def cb_confirm_order(callback: CallbackQuery, state: FSMContext) -> None:
    """Confirm -> create the order through the ERP (price resolved server-side)."""
    await callback.answer()
    chat_id = str(callback.message.chat.id)
    token = get_token(chat_id)
    rep_id = _rep_id_for(chat_id)

    data = await state.get_data()
    lines = data.get("lines", [])
    if not lines:
        await state.clear()
        await callback.message.answer(
            "❌ سفارش خالی است.",
            reply_markup=_main_menu_keyboard(),
        )
        return

    payload = [
        {"product_id": ln["product_id"], "qty_ordered": ln["qty"]}
        for ln in lines
    ]

    await callback.message.answer("⏳ در حال ثبت سفارش...")

    try:
        result = await api_create_order(
            token, rep_id, data["customer_id"], payload,
        )
    except BotApiError as exc:
        if exc.status_code in (401, 403):
            await _reauth(callback.message, state)
            return
        # Business failure (no price list, inactive list, out of scope,
        # credit limit, ...) -- keep the draft so the rep can retry or
        # cancel instead of starting over.
        logger.warning("Order creation rejected: %s", exc.detail)
        await callback.message.answer(
            "❌ ثبت سفارش ناموفق بود.\n"
            "لطفاً موجودی، لیست قیمت مشتری و سقف اعتبار را در ERP بررسی کنید.",
            reply_markup=_review_keyboard(),
        )
        return
    except Exception:
        logger.exception("Order creation failed")
        await callback.message.answer(
            "❌ خطا در ارتباط با سرور. سفارش ثبت نشد؛ لطفاً دوباره تلاش کنید.",
            reply_markup=_review_keyboard(),
        )
        return

    await state.clear()
    await callback.message.answer(
        format_order_created(result)
        + "\n\n📌 فاکتور پس از تأیید و ارسال سفارش در ERP صادر می‌شود.",
        reply_markup=_main_menu_keyboard(),
    )


@router.callback_query(F.data == "action:cancel")
async def cb_cancel_order(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel the whole order flow (available from any step)."""
    await callback.answer()
    await state.clear()
    await callback.message.answer(
        "❌ ثبت سفارش لغو شد.",
        reply_markup=_main_menu_keyboard(),
    )


@router.callback_query(F.data.startswith("page:"))
async def cb_page(callback: CallbackQuery, state: FSMContext) -> None:
    """Pagination for the customer/product inline lists."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        return
    _, kind, offset_str = parts
    try:
        offset = int(offset_str)
    except ValueError:
        return

    await callback.answer()
    data = await state.get_data()

    if kind == "cust":
        items = data.get("customers", [])
        label_fn = _customer_button_text
        header = "لطفاً مشتری را انتخاب کنید:"
        page_key = "customer_page"
    elif kind == "prod":
        items = data.get("products", [])
        label_fn = _product_button_text
        header = "لطفاً محصول را انتخاب کنید:"
        page_key = "product_page"
    else:
        return

    await callback.message.edit_text(
        header,
        reply_markup=_paged_keyboard(
            items, kind=kind, page=offset, label_fn=label_fn
        ),
    )
    await state.update_data(**{page_key: offset})


@router.message(
    StateFilter(OrderFlow.selecting_customer, OrderFlow.selecting_product)
)
async def msg_wrong_input_in_list(message: Message, state: FSMContext) -> None:
    """Free text while a list is showing -> re-show the matching list."""
    data = await state.get_data()
    if await state.get_state() == OrderFlow.selecting_customer.state:
        await message.answer(
            "لطفاً از دکمه‌های زیر مشتری را انتخاب کنید:",
            reply_markup=_paged_keyboard(
                data.get("customers", []),
                kind="cust",
                page=data.get("customer_page", 0),
                label_fn=_customer_button_text,
            ),
        )
    else:
        await message.answer(
            "لطفاً از دکمه‌های زیر محصول را انتخاب کنید:",
            reply_markup=_paged_keyboard(
                data.get("products", []),
                kind="prod",
                page=data.get("product_page", 0),
                label_fn=_product_button_text,
            ),
        )


@router.message(F.text == "🚪 خروج / قطع اتصال")
async def handle_logout(message: Message, state: FSMContext) -> None:
    """Handle 'Logout / Disconnect' — revoke the persistent session."""
    chat_id = str(message.chat.id)
    token = get_token(chat_id)
    clear_token(chat_id)
    await state.clear()

    if token is not None:
        try:
            await api_logout(token)
        except BotApiError as exc:
            logger.warning("Logout API error (session may already be gone): %s", exc.detail)
        except Exception:
            logger.exception("Logout failed (session remains valid on backend)")

    await message.answer(
        "🚪 از ربات خارج شدید.\n"
        "برای اتصال دوباره، شماره موبایل خود را به اشتراک بگذارید.",
        reply_markup=_contact_keyboard(),
    )


async def _handle_auth_error(message: Message, exc: BotApiError, action: str) -> None:
    """Handle a bot API error, prompting re-auth when the session is gone."""
    chat_id = str(message.chat.id)
    if exc.status_code in (401, 403):
        clear_token(chat_id)
        await message.answer(
            "❌ نشست شما منقضی شده است. لطفاً دوباره شماره موبایل خود را "
            "به اشتراک بگذارید.",
            reply_markup=_contact_keyboard(),
        )
    else:
        await message.answer(
            f"❌ خطا در {action}: {exc.detail}",
            reply_markup=_main_menu_keyboard(),
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def create_telegram_session() -> AiohttpSession:
    """Create the Telegram API session using the optional local proxy."""
    return AiohttpSession(proxy=get_telegram_proxy())


async def main() -> None:
    """Start the Telegram bot."""
    set_platform(PLATFORM)
    token = get_telegram_token()
    bot = Bot(token=token, session=create_telegram_session())
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