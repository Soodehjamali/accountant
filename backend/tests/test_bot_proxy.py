"""Focused tests for Telegram proxy configuration."""

from __future__ import annotations

import pytest

from bots import config


def test_telegram_proxy_is_unset_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_PROXY", raising=False)

    assert config.get_telegram_proxy() is None


def test_telegram_proxy_accepts_sing_box_http_and_socks_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for value in (
        "http://127.0.0.1:2080",
        "socks5://127.0.0.1:2081",
        "socks5h://user:password@127.0.0.1:2081",
    ):
        monkeypatch.setenv("TELEGRAM_PROXY", value)
        assert config.get_telegram_proxy() == value


def test_telegram_proxy_rejects_unsupported_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_PROXY", "ftp://127.0.0.1:2080")

    with pytest.raises(RuntimeError, match="TELEGRAM_PROXY"):
        config.get_telegram_proxy()


@pytest.mark.parametrize(
    "value",
    ["http://127.0.0.1", "http://:2080", "http://127.0.0.1:not-a-port"],
)
def test_telegram_proxy_requires_host_and_port(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("TELEGRAM_PROXY", value)

    with pytest.raises(RuntimeError, match="host and port"):
        config.get_telegram_proxy()


def test_telegram_session_receives_configured_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bots.telegram_bot as telegram_bot

    captured: dict[str, object] = {}

    class FakeAiohttpSession:
        def __init__(self, *, proxy: str | None) -> None:
            captured["proxy"] = proxy

    monkeypatch.setenv("TELEGRAM_PROXY", "socks5://127.0.0.1:2081")
    monkeypatch.setattr(telegram_bot, "AiohttpSession", FakeAiohttpSession)

    telegram_bot.create_telegram_session()

    assert captured["proxy"] == "socks5://127.0.0.1:2081"


def test_telegram_session_has_no_proxy_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bots.telegram_bot as telegram_bot

    captured: dict[str, object] = {}

    class FakeAiohttpSession:
        def __init__(self, *, proxy: str | None) -> None:
            captured["proxy"] = proxy

    monkeypatch.delenv("TELEGRAM_PROXY", raising=False)
    monkeypatch.setattr(telegram_bot, "AiohttpSession", FakeAiohttpSession)

    telegram_bot.create_telegram_session()

    assert captured["proxy"] is None
