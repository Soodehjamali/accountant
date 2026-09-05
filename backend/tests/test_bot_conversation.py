"""Focused unit tests for the Telegram order-creation conversation.

These tests drive the handlers in ``bots/telegram_bot.py`` directly with a
fake bot/session (no real Telegram connection, no backend, no database).
The backend REST endpoints they call are mocked at the ``bots.telegram_bot``
module level, so the conversation state machine -- FSM transitions, inline
keyboards, quantity validation, price resolution flow, order submission
payload, cancel/logout -- is verified end to end.

Mirrors the acceptance list:
1.  /start still requires phone
2.  successful phone verification shows the main menu
3.  the invoice/order flow starts (customer list)
4.  customer selection
5.  product selection
6.  quantity
7.  price display (ERP-resolved, never user-supplied)
8.  add another product
9.  confirmation (order created with the correct payload, no price)
10. cancel
11. missing price -> controlled Persian error
12. backend failure -> friendly Persian error
"""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    Contact,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    User,
)

import bots.shared as shared
import bots.telegram_bot as tb

CHAT_ID = 123


class FakeSession:
    """Records every outgoing Telegram API method instead of calling the API."""

    def __init__(self) -> None:
        self.calls: list = []

    async def __call__(self, bot, method, timeout=None):  # noqa: ANN001
        self.calls.append(method)
        return None


@pytest.fixture()
def bot() -> Bot:
    return Bot(token="123456:testtoken", session=FakeSession())


@pytest.fixture()
def ctx(bot) -> dict:
    """Fresh FSM context + empty token cache per test."""
    storage = MemoryStorage()
    key = StorageKey(bot_id=bot.id, chat_id=CHAT_ID, user_id=CHAT_ID)
    state = FSMContext(storage=storage, key=key)
    shared.clear_token(str(CHAT_ID))
    return {"bot": bot, "storage": storage, "key": key, "state": state}


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

REP_ID = str(uuid.uuid4())
CUSTOMER_A = {
    "id": str(uuid.uuid4()),
    "code": "CUST-A",
    "name": "شرکت الف",
    "currency_id": str(uuid.uuid4()),
}
CUSTOMER_B = {
    "id": str(uuid.uuid4()),
    "code": "CUST-B",
    "name": "شرکت ب",
    "currency_id": str(uuid.uuid4()),
}
PRODUCT_A = {
    "product_id": str(uuid.uuid4()),
    "sku": "SKU-A",
    "name": "محصول الف",
    "balance": 100,
}
PRODUCT_B = {
    "product_id": str(uuid.uuid4()),
    "sku": "SKU-B",
    "name": "محصول ب",
    "balance": 50,
}
PRICE_PREVIEW_A = {
    "product_id": PRODUCT_A["product_id"],
    "product_sku": PRODUCT_A["sku"],
    "product_name": PRODUCT_A["name"],
    "unit_price": 50000.0,
    "currency_id": str(uuid.uuid4()),
    "price_list_id": str(uuid.uuid4()),
    "price_type": "RETAIL",
}


# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------


def make_message(bot: Bot, *, text: str = "", contact: Contact | None = None) -> Message:
    msg = Message.model_construct(
        message_id=1,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=Chat.model_construct(id=CHAT_ID, type="private"),
        from_user=User.model_construct(id=CHAT_ID, is_bot=False, first_name="T"),
        text=text,
        contact=contact,
    )
    msg._bot = bot  # type: ignore[attr-defined]
    return msg


def make_callback(bot: Bot, data: str) -> CallbackQuery:
    cb = CallbackQuery.model_construct(
        id="cb-1",
        from_user=User.model_construct(id=CHAT_ID, is_bot=False, first_name="T"),
        chat_instance="ci",
        message=make_message(bot),
        data=data,
    )
    cb._bot = bot  # type: ignore[attr-defined]
    return cb


def run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def run_and_drain(coro) -> None:  # noqa: ANN001
    """Run a coroutine and also finish tasks the handlers scheduled with
    ``asyncio.create_task`` (e.g. the re-auth prompt in ``_auth_required``)."""

    async def inner() -> None:
        await coro
        pending = [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task()
        ]
        if pending:
            await asyncio.gather(*pending)

    asyncio.run(inner())


def sent_messages(bot: Bot) -> list:
    return [c for c in bot.session.calls if isinstance(c, (SendMessage, EditMessageText))]


def last_message(bot: Bot):
    msgs = sent_messages(bot)
    assert msgs, "no messages were sent"
    return msgs[-1]


def inline_buttons(markup) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [
        button.text
        for row in markup.inline_keyboard
        for button in row
    ]


def inline_callback_data(markup) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [
        button.callback_data or ""
        for row in markup.inline_keyboard
        for button in row
    ]


def login() -> None:
    """Simulate a successful phone verification for the test chat."""
    shared.store_token(str(CHAT_ID), "jwt-token", REP_ID)


async def seed_customer_selection(state: FSMContext) -> None:
    await state.set_state(tb.OrderFlow.selecting_customer)
    await state.update_data(customers=[CUSTOMER_A, CUSTOMER_B], customer_page=0)


async def seed_product_selection(state: FSMContext) -> None:
    await state.set_state(tb.OrderFlow.selecting_product)
    await state.update_data(
        customer_id=CUSTOMER_A["id"],
        customer_name=CUSTOMER_A["name"],
        products=[
            {"id": p["product_id"], "sku": p["sku"], "name": p["name"], "balance": p["balance"]}
            for p in (PRODUCT_A, PRODUCT_B)
        ],
        product_page=0,
        pending_product_id=None,
    )


async def seed_quantity_entry(state: FSMContext) -> None:
    await seed_product_selection(state)
    await state.set_state(tb.OrderFlow.entering_quantity)
    await state.update_data(pending_product_id=PRODUCT_A["product_id"])


# ---------------------------------------------------------------------------
# 1. /start still requires phone
# ---------------------------------------------------------------------------


def test_start_still_requires_phone(ctx) -> None:
    run(tb.cmd_start(make_message(ctx["bot"])))
    msg = last_message(ctx["bot"])
    assert "اشتراک" in msg.text
    assert isinstance(msg.reply_markup, ReplyKeyboardMarkup)


# ---------------------------------------------------------------------------
# 2. Phone verification -> main menu
# ---------------------------------------------------------------------------


def test_phone_verification_shows_main_menu(ctx, monkeypatch) -> None:
    async def fake_verify_phone(**kwargs):  # noqa: ANN003
        return {
            "access_token": "jwt-token",
            "representative_id": REP_ID,
            "representative_name": "علی احمدی",
        }

    monkeypatch.setattr(tb, "api_verify_phone", fake_verify_phone)

    run(
        tb.handle_contact(
            make_message(
                ctx["bot"],
                contact=Contact.model_construct(
                    phone_number="+989123456789",
                    first_name="علی",
                    user_id=CHAT_ID,
                ),
            )
        )
    )

    sent = last_message(ctx["bot"])
    assert "خوش آمدید، علی احمدی" in sent.text
    assert isinstance(sent.reply_markup, ReplyKeyboardMarkup)
    # The representative id from the verified login is cached for API calls.
    assert shared.get_rep_id(str(CHAT_ID)) == REP_ID


# ---------------------------------------------------------------------------
# 3. Flow starts (customer list)
# ---------------------------------------------------------------------------


def test_order_flow_starts_with_customer_list(ctx, monkeypatch) -> None:
    login()

    async def fake_customers(token: str, rep_id: str):  # noqa: ANN001
        assert rep_id == REP_ID  # real rep id, never "self"
        return {"items": [CUSTOMER_A, CUSTOMER_B]}

    monkeypatch.setattr(tb, "api_get_customers", fake_customers)

    run(tb.handle_create_invoice_start(make_message(ctx["bot"]), ctx["state"]))

    msg = last_message(ctx["bot"])
    assert "مشتری" in msg.text
    buttons = inline_buttons(msg.reply_markup)
    assert any(CUSTOMER_A["code"] in b for b in buttons)
    assert any(CUSTOMER_B["code"] in b for b in buttons)
    callbacks = inline_callback_data(msg.reply_markup)
    assert any(b.startswith("cust:") for b in callbacks)
    assert run_state(ctx["state"]) == tb.OrderFlow.selecting_customer.state


def test_flow_start_no_customers(ctx, monkeypatch) -> None:
    login()

    async def fake_customers(token: str, rep_id: str):  # noqa: ANN001
        return {"items": []}

    monkeypatch.setattr(tb, "api_get_customers", fake_customers)

    run(tb.handle_create_invoice_start(make_message(ctx["bot"]), ctx["state"]))

    msg = last_message(ctx["bot"])
    assert "هیچ مشتری" in msg.text
    assert run_state(ctx["state"]) is None


# ---------------------------------------------------------------------------
# 4. Customer selection -> product list
# ---------------------------------------------------------------------------


def test_customer_selection_shows_products(ctx, monkeypatch) -> None:
    login()
    run_and_drain(seed_customer_selection(ctx["state"]))

    async def fake_products(token: str, rep_id: str):  # noqa: ANN001
        return {"items": [PRODUCT_A, PRODUCT_B], "warehouse_code": "WH-1"}

    monkeypatch.setattr(tb, "api_get_products", fake_products)

    run(tb.cb_select_customer(make_callback(ctx["bot"], f"cust:{CUSTOMER_A['id']}"), ctx["state"]))

    msg = last_message(ctx["bot"])
    assert "محصول" in msg.text
    buttons = inline_buttons(msg.reply_markup)
    assert any(PRODUCT_A["sku"] in b for b in buttons)
    assert any(PRODUCT_B["sku"] in b for b in buttons)
    callbacks = inline_callback_data(msg.reply_markup)
    assert any(b.startswith("prod:") for b in callbacks)
    assert run_state(ctx["state"]) == tb.OrderFlow.selecting_product.state
    data = run_data(ctx["state"])
    assert data["customer_id"] == CUSTOMER_A["id"]


# ---------------------------------------------------------------------------
# 5. Product selection -> quantity prompt
# ---------------------------------------------------------------------------


def test_product_selection_asks_quantity(ctx) -> None:
    login()
    run_and_drain(seed_product_selection(ctx["state"]))

    run(
        tb.cb_select_product(
            make_callback(ctx["bot"], f"prod:{PRODUCT_A['product_id']}"),
            ctx["state"],
        )
    )

    msg = last_message(ctx["bot"])
    assert "تعداد را وارد کنید" in msg.text
    assert "موجودی فعلی: 100" in msg.text
    assert run_state(ctx["state"]) == tb.OrderFlow.entering_quantity.state


# ---------------------------------------------------------------------------
# 6. Quantity -> ERP price + review
# ---------------------------------------------------------------------------


def test_quantity_resolves_price_and_reviews(ctx, monkeypatch) -> None:
    login()
    run_and_drain(seed_quantity_entry(ctx["state"]))

    captured: dict = {}

    async def fake_price_preview(token: str, rep_id: str, customer_id: str, product_id: str):  # noqa: ANN001
        captured["customer_id"] = customer_id
        captured["product_id"] = product_id
        return PRICE_PREVIEW_A

    monkeypatch.setattr(tb, "api_price_preview", fake_price_preview)

    run(tb.msg_enter_quantity(make_message(ctx["bot"], text="10"), ctx["state"]))

    # The ERP resolved the price -- the bot never supplied one.
    assert captured["customer_id"] == CUSTOMER_A["id"]
    assert captured["product_id"] == PRODUCT_A["product_id"]

    msg = last_message(ctx["bot"])
    assert "پیش‌نویس سفارش" in msg.text
    assert "50,000" in msg.text  # unit price formatted
    assert "جمع کل: 500,000" in msg.text  # 10 x 50,000
    assert run_state(ctx["state"]) == tb.OrderFlow.add_more_or_confirm.state

    data = run_data(ctx["state"])
    assert len(data["lines"]) == 1
    assert data["lines"][0]["unit_price"] == 50000.0


def test_invalid_quantity_rejected(ctx, monkeypatch) -> None:
    login()
    run_and_drain(seed_quantity_entry(ctx["state"]))

    async def fake_price_preview(token, rep_id, customer_id, product_id):  # noqa: ANN001
        return PRICE_PREVIEW_A

    monkeypatch.setattr(tb, "api_price_preview", fake_price_preview)

    for bad in ("abc", "0", "-5", "۱۲.۳.۴"):
        run(tb.msg_enter_quantity(make_message(ctx["bot"], text=bad), ctx["state"]))
        msg = last_message(ctx["bot"])
        assert "معتبر نیست" in msg.text
        # Still waiting for a valid quantity.
        assert run_state(ctx["state"]) == tb.OrderFlow.entering_quantity.state


# ---------------------------------------------------------------------------
# 7. Add another product
# ---------------------------------------------------------------------------


def test_add_another_product_returns_to_product_list(ctx) -> None:
    login()
    run_and_drain(seed_product_selection(ctx["state"]))
    await_set_state(ctx["state"], tb.OrderFlow.add_more_or_confirm)
    await_update(ctx["state"], lines=[], pending_product_id=None)

    run(tb.cb_add_more(make_callback(ctx["bot"], "action:add"), ctx["state"]))

    msg = last_message(ctx["bot"])
    assert "محصول" in msg.text
    callbacks = inline_callback_data(msg.reply_markup)
    assert any(b.startswith("prod:") for b in callbacks)
    assert run_state(ctx["state"]) == tb.OrderFlow.selecting_product.state


# ---------------------------------------------------------------------------
# 8. Confirmation -> order created via ERP
# ---------------------------------------------------------------------------


def test_confirm_creates_order_without_price(ctx, monkeypatch) -> None:
    login()
    run_and_drain(seed_quantity_entry(ctx["state"]))
    await_update(
        ctx["state"],
        lines=[
            {
                "product_id": PRODUCT_A["product_id"],
                "sku": PRODUCT_A["sku"],
                "name": PRODUCT_A["name"],
                "qty": 10.0,
                "unit_price": 50000.0,
                "line_total": 500000.0,
            }
        ],
    )
    await_set_state(ctx["state"], tb.OrderFlow.add_more_or_confirm)

    captured: dict = {}

    async def fake_create_order(token: str, rep_id: str, customer_id: str, lines: list):  # noqa: ANN001
        captured["rep_id"] = rep_id
        captured["customer_id"] = customer_id
        captured["lines"] = lines
        return {
            "order_id": str(uuid.uuid4()),
            "order_number": "ORD-20260904-ABCD1234",
            "state": "DRAFT",
            "subtotal": 500000.0,
            "grand_total": 500000.0,
            "currency_id": str(uuid.uuid4()),
            "lines": [
                {
                    "product_id": PRODUCT_A["product_id"],
                    "product_sku": PRODUCT_A["sku"],
                    "product_name": PRODUCT_A["name"],
                    "qty_ordered": 10.0,
                    "unit_price": 50000.0,
                    "line_total": 500000.0,
                }
            ],
        }

    monkeypatch.setattr(tb, "api_create_order", fake_create_order)

    run(
        tb.cb_confirm_order(
            make_callback(ctx["bot"], "action:confirm"), ctx["state"],
        )
    )

    # Correct, scope-safe payload: real rep id, scoped customer, qty only.
    assert captured["rep_id"] == REP_ID
    assert captured["customer_id"] == CUSTOMER_A["id"]
    assert captured["lines"] == [
        {"product_id": PRODUCT_A["product_id"], "qty_ordered": 10.0}
    ]
    # The bot must never send a price.
    assert all("unit_price" not in line for line in captured["lines"])

    msg = last_message(ctx["bot"])
    assert "سفارش با موفقیت ثبت شد" in msg.text
    assert "ORD-20260904-ABCD1234" in msg.text
    assert "پیش‌نویس" in msg.text  # DRAFT state in Persian
    assert run_state(ctx["state"]) is None  # flow finished


def test_confirm_without_lines_clears(ctx) -> None:
    login()
    await_update(ctx["state"], lines=[])
    await_set_state(ctx["state"], tb.OrderFlow.add_more_or_confirm)

    run(tb.cb_confirm_order(make_callback(ctx["bot"], "action:confirm"), ctx["state"]))

    msg = last_message(ctx["bot"])
    assert "سفارش خالی" in msg.text
    assert run_state(ctx["state"]) is None


# ---------------------------------------------------------------------------
# 9. Cancel
# ---------------------------------------------------------------------------


def test_cancel_clears_state(ctx) -> None:
    login()
    run_and_drain(seed_customer_selection(ctx["state"]))

    run(tb.cb_cancel_order(make_callback(ctx["bot"], "action:cancel"), ctx["state"]))

    msg = last_message(ctx["bot"])
    assert "لغو شد" in msg.text
    assert run_state(ctx["state"]) is None


# ---------------------------------------------------------------------------
# 10. Missing price -> controlled Persian error, back to product list
# ---------------------------------------------------------------------------


def test_missing_price_returns_controlled_error(ctx, monkeypatch) -> None:
    login()
    run_and_drain(seed_quantity_entry(ctx["state"]))

    async def fake_price_preview(token, rep_id, customer_id, product_id):  # noqa: ANN001
        raise tb.BotApiError(
            422, "No currently valid price for product 'x' in price list 'y'."
        )

    monkeypatch.setattr(tb, "api_price_preview", fake_price_preview)

    run(tb.msg_enter_quantity(make_message(ctx["bot"], text="5"), ctx["state"]))

    texts = [m.text for m in sent_messages(ctx["bot"])]
    assert any("قیمت فروش فعالی" in t for t in texts)
    assert any("لیست قیمت" in t for t in texts)
    # Back to product selection so the rep can pick another product.
    assert run_state(ctx["state"]) == tb.OrderFlow.selecting_product.state


# ---------------------------------------------------------------------------
# 11. Backend failure -> friendly Persian error
# ---------------------------------------------------------------------------


def test_backend_failure_shows_friendly_error(ctx, monkeypatch) -> None:
    login()

    async def fake_customers(token: str, rep_id: str):  # noqa: ANN001
        raise RuntimeError("connection refused")

    monkeypatch.setattr(tb, "api_get_customers", fake_customers)

    run(tb.handle_create_invoice_start(make_message(ctx["bot"]), ctx["state"]))

    msg = last_message(ctx["bot"])
    assert "خطا در ارتباط با سرور" in msg.text
    assert run_state(ctx["state"]) is None


def test_auth_failure_prompts_reauth(ctx, monkeypatch) -> None:
    login()

    async def fake_customers(token: str, rep_id: str):  # noqa: ANN001
        raise tb.BotApiError(401, "Bot session has expired.")

    monkeypatch.setattr(tb, "api_get_customers", fake_customers)

    run(tb.handle_create_invoice_start(make_message(ctx["bot"]), ctx["state"]))

    msg = last_message(ctx["bot"])
    assert "نشست شما منقضی شده" in msg.text
    assert isinstance(msg.reply_markup, ReplyKeyboardMarkup)
    assert shared.get_token(str(CHAT_ID)) is None  # token cache cleared
    assert run_state(ctx["state"]) is None


# ---------------------------------------------------------------------------
# 12. Logout clears flow state
# ---------------------------------------------------------------------------


def test_logout_clears_state_and_token(ctx, monkeypatch) -> None:
    login()
    await_update(ctx["state"], lines=[], customer_id=CUSTOMER_A["id"])
    await_set_state(ctx["state"], tb.OrderFlow.selecting_product)

    async def fake_logout(token: str) -> None:
        return None

    monkeypatch.setattr(tb, "api_logout", fake_logout)

    run(tb.handle_logout(make_message(ctx["bot"]), ctx["state"]))

    assert shared.get_token(str(CHAT_ID)) is None
    assert shared.get_rep_id(str(CHAT_ID)) is None
    assert run_state(ctx["state"]) is None


# ---------------------------------------------------------------------------
# Free text while a list is showing -> re-show the matching list
# ---------------------------------------------------------------------------


def test_free_text_while_selecting_product_reshows_products(ctx) -> None:
    login()
    run_and_drain(seed_product_selection(ctx["state"]))

    run(
        tb.msg_wrong_input_in_list(
            make_message(ctx["bot"], text="متن آزاد"), ctx["state"],
        )
    )

    msg = last_message(ctx["bot"])
    assert "محصول" in msg.text
    callbacks = inline_callback_data(msg.reply_markup)
    assert any(b.startswith("prod:") for b in callbacks)
    assert not any(b.startswith("cust:") for b in callbacks)


def test_free_text_while_selecting_customer_reshows_customers(ctx) -> None:
    login()
    run_and_drain(seed_customer_selection(ctx["state"]))

    run(
        tb.msg_wrong_input_in_list(
            make_message(ctx["bot"], text="متن آزاد"), ctx["state"],
        )
    )

    msg = last_message(ctx["bot"])
    assert "مشتری" in msg.text
    callbacks = inline_callback_data(msg.reply_markup)
    assert any(b.startswith("cust:") for b in callbacks)
    assert not any(b.startswith("prod:") for b in callbacks)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_pagination_next_page(ctx) -> None:
    login()
    many = [
        {"id": str(uuid.uuid4()), "code": f"C{i}", "name": f"مشتری {i}", "currency_id": str(uuid.uuid4())}
        for i in range(tb.PAGE_SIZE + 2)
    ]
    await_set_state(ctx["state"], tb.OrderFlow.selecting_customer)
    await_update(ctx["state"], customers=many, customer_page=0)

    run(tb.cb_page(make_callback(ctx["bot"], "page:cust:1"), ctx["state"]))

    msg = last_message(ctx["bot"])
    assert isinstance(msg, EditMessageText)
    buttons = inline_buttons(msg.reply_markup)
    assert any(many[-1]["code"] in b for b in buttons)
    callbacks = inline_callback_data(msg.reply_markup)
    assert any(b == "page:cust:0" for b in callbacks)  # previous page present


# ---------------------------------------------------------------------------
# Small helpers to keep the state assertions readable
# ---------------------------------------------------------------------------


def run_state(state: FSMContext) -> str | None:
    return run(state.get_state())


def run_data(state: FSMContext) -> dict:
    return run(state.get_data())


def await_set_state(state: FSMContext, s) -> None:  # noqa: ANN001
    run(state.set_state(s))


def await_update(state: FSMContext, **kwargs) -> None:  # noqa: ANN003
    run(state.update_data(**kwargs))


__all__: list[str] = []