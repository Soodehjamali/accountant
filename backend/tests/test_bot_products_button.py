"""Unit tests for the bot's «📦 لیست محصولات» button (bots/telegram_bot.py).

The button mirrors the «📦 موجودی انبار من» button: it calls the existing
``GET /api/v1/bot/reps/{rep_id}/products`` REST endpoint (via
``bots.shared.api_get_products``) and renders the ``BotProductListResponse``
(``items`` / ``warehouse_code``) as a readable Persian message.

These tests cover the pure formatting/keyboard logic -- no Telegram network
and no backend HTTP are touched.  The endpoint-side authorization states
(403 without ``BOT_QUERY``, 401 without a valid session, empty warehouse,
no warehouse -> ``"N/A"``) are covered by the live-DB endpoint tests in
``test_bot_order_flow.py::TestBotProducts`` and ``test_bot_phone_verification.py``.
"""

from __future__ import annotations

from bots.telegram_bot import _format_products_text, _main_menu_keyboard


class TestFormatProductsText:
    def test_multiple_items_each_row_is_name_sku_balance(self) -> None:
        data = {
            "warehouse_code": "WH-MAIN",
            "items": [
                {"product_id": "p1", "sku": "SKU-1", "name": "قند", "balance": 120},
                {"product_id": "p2", "sku": "SKU-2", "name": "چای", "balance": 5},
            ],
        }
        text = _format_products_text(data)

        assert "WH-MAIN" in text
        assert "قند — SKU-1 — موجودی: 120" in text
        assert "چای — SKU-2 — موجودی: 5" in text

    def test_empty_items_shows_no_products_message(self) -> None:
        text = _format_products_text({"warehouse_code": "WH-MAIN", "items": []})
        assert text == "محصولی در انبار شما موجود نیست."

    def test_no_warehouse_shows_no_warehouse_message(self) -> None:
        text = _format_products_text({"warehouse_code": "N/A", "items": []})
        assert text == "هیچ انباری به شما اختصاص داده نشده است."

    def test_missing_warehouse_code_defaults_to_na(self) -> None:
        # The backend always sends warehouse_code, but defaulting to "N/A"
        # keeps the message correct for unexpected payloads too.
        text = _format_products_text({"items": []})
        assert text == "هیچ انباری به شما اختصاص داده نشده است."


class TestMainMenuKeyboard:
    def test_products_button_is_in_main_menu(self) -> None:
        keyboard = _main_menu_keyboard()
        texts = {button.text for row in keyboard.keyboard for button in row}
        assert "📦 لیست محصولات" in texts


__all__: list[str] = []