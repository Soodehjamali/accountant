"""Focused tests for the admin bot-configuration feature.

Covers the 11 acceptance scenarios from the bot-settings phase:

1.  admin can read bot settings without receiving the raw token
2.  non-admin cannot modify bot settings
3.  Telegram token is stored securely (encrypted at rest)
4.  Telegram getMe success (identity persisted, no token echoed)
5.  Telegram invalid token (useful error, no fake success)
6.  Bale connection success / failure (same getMe pattern, Bale base URL)
7.  frontend save flow                      -> frontend/src/.../*.test.tsx
8.  frontend does not render the raw token  -> frontend/src/.../*.test.tsx
9.  bot runtime receives the persisted configuration
10. configured-but-stopped bot is not shown as connected
11. secrets do not appear in logs

Follows the project's test conventions (see test_rbac.py): live PostgreSQL
via ``get_session_factory()``, admin user bootstrapped with the ADMIN role
for API tests, ``httpx.get`` patched for the real Telegram/Bale getMe call.
"""

from __future__ import annotations

import json
import logging
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from database.models.bot_config import BotConfig
from database.models.bot_platform_ref import BotPlatformRef
from database.session import get_session_factory
from services import auth_service, bootstrap_service, bot_config_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB bot-config tests",
)

RUNTIME_SECRET = "dev-bot-runtime-secret"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _create_user_with_role(role_code: str | None) -> dict[str, str]:
    """Create a fresh user (optionally assigned ``role_code``) and return
    Authorization headers for it."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"botcfg_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )
        if role_code is not None:
            rbac_service.assign_role(
                session,
                user_id=new_user.id,
                role_code=role_code,
                assigned_by=system_user.id,
            )
        session.commit()
    finally:
        session.close()

    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session2 = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session2, username_or_email=username, password=password
        )
        assert user is not None
        session2.commit()
        token = create_access_token(
            subject=str(user.id),
            secret_key=settings.secret_key,
            expires_in_seconds=settings.access_token_expire_minutes * 60,
        )
    finally:
        session2.close()

    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_auth_headers() -> dict[str, str]:
    """Headers for a user with the ADMIN role (which holds BOT_MANAGE)."""
    return _create_user_with_role(bootstrap_service.ADMIN_ROLE_CODE)


@pytest.fixture()
def plain_auth_headers() -> dict[str, str]:
    """Headers for a user with no roles at all (cannot modify bot settings)."""
    return _create_user_with_role(None)


def _save_token_via_api(
    client: TestClient, headers: dict[str, str], platform: str, token: str, enabled: bool = True
) -> None:
    """Save a token for ``platform`` through the admin API."""
    response = client.put(
        f"/api/v1/bot-config/{platform}",
        json={"enabled": enabled, "token": token},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _fake_getme_ok(platform_base: str | None = None):
    """Return an ``httpx.get`` replacement that answers a successful getMe."""
    import httpx

    def fake_get(url: str, **kwargs):  # noqa: ANN001, ANN202
        assert f"/bot" in url and url.endswith("/getMe")
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": 123456789,
                    "is_bot": True,
                    "first_name": "Example Bot",
                    "username": "example_bot",
                },
            },
        )

    return fake_get


def _fake_getme_invalid(platform_base: str | None = None):
    """Return an ``httpx.get`` replacement that answers an invalid token."""
    import httpx

    def fake_get(url: str, **kwargs):  # noqa: ANN001, ANN202
        return httpx.Response(
            401,
            json={"ok": False, "error_code": 401, "description": "Unauthorized"},
        )

    return fake_get


# ===========================================================================
# 1. Admin can read bot settings without receiving the raw token
# ===========================================================================


@requires_database
def test_admin_reads_config_without_raw_token(
    client: TestClient, admin_auth_headers: dict[str, str]
) -> None:
    secret = f"1234567890:ABCDEF_{uuid.uuid4().hex[:6]}"
    _save_token_via_api(client, admin_auth_headers, "telegram", secret)

    response = client.get("/api/v1/bot-config", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    telegram = next(item for item in body["items"] if item["platform"] == "TELEGRAM")
    assert telegram["token_configured"] is True
    assert telegram["token_hint"] == secret[-4:]

    raw = json.dumps(body)
    assert secret not in raw, "GET must never return the raw token"
    assert "token_ciphertext" not in raw
    assert "token" not in telegram, "item must not carry a token field"


# ===========================================================================
# 2. Non-admin cannot modify bot settings
# ===========================================================================


@requires_database
def test_non_admin_cannot_modify_bot_settings(
    client: TestClient, plain_auth_headers: dict[str, str]
) -> None:
    response = client.put(
        "/api/v1/bot-config/telegram",
        json={"enabled": True, "token": "1234567890:SECRET"},
        headers=plain_auth_headers,
    )
    assert response.status_code == 403

    # The GET surface is equally gated.
    response = client.get("/api/v1/bot-config", headers=plain_auth_headers)
    assert response.status_code == 403


# ===========================================================================
# 3. Telegram token is stored securely (encrypted at rest)
# ===========================================================================


@requires_database
def test_telegram_token_stored_encrypted() -> None:
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        secret = f"9876543210:RAW_SECRET_{uuid.uuid4().hex[:6]}"

        bot_config_service.save_config(
            session,
            "TELEGRAM",
            enabled=True,
            token=secret,
            updated_by=system_user.id,
            secret_key="test-secret-key",
        )
        session.flush()

        platform = session.execute(
            select(BotPlatformRef).where(BotPlatformRef.code == "TELEGRAM")
        ).scalar_one()
        row = session.execute(
            select(BotConfig).where(BotConfig.bot_platform_id == platform.id)
        ).scalar_one()

        assert row.token_ciphertext is not None
        assert secret not in row.token_ciphertext, "raw token must not appear in ciphertext"

        # Decryption round-trips (the runtime path).
        plain = bot_config_service.decrypt_token(
            row.token_ciphertext, secret_key="test-secret-key"
        )
        assert plain == secret
    finally:
        session.close()


# ===========================================================================
# 4. Telegram getMe success (real API call, mocked transport)
# ===========================================================================


@requires_database
def test_telegram_getme_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.get", _fake_getme_ok())

    ok, detail, identity = bot_config_service.test_connection(
        "TELEGRAM", "1234567890:VALID"
    )
    assert ok is True
    assert "@example_bot" in detail
    assert identity == {
        "bot_id": "123456789",
        "username": "example_bot",
        "name": "Example Bot",
    }


@requires_database
def test_telegram_test_endpoint_persists_identity(
    client: TestClient, admin_auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("httpx.get", _fake_getme_ok())
    secret = f"1234567890:PERSIST_{uuid.uuid4().hex[:6]}"
    _save_token_via_api(client, admin_auth_headers, "telegram", secret)

    response = client.post(
        "/api/v1/bot-config/telegram/test", headers=admin_auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["bot_username"] == "example_bot"
    assert body["bot_name"] == "Example Bot"
    assert secret not in json.dumps(body), "test response must not echo the token"

    # Identity now shows up on the read surface (without the token).
    response = client.get("/api/v1/bot-config", headers=admin_auth_headers)
    telegram = next(item for item in response.json()["items"] if item["platform"] == "TELEGRAM")
    assert telegram["bot_username"] == "example_bot"
    assert telegram["bot_name"] == "Example Bot"


# ===========================================================================
# 5. Telegram invalid token
# ===========================================================================


@requires_database
def test_telegram_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.get", _fake_getme_invalid())

    ok, detail, identity = bot_config_service.test_connection(
        "TELEGRAM", "1234567890:WRONG"
    )
    assert ok is False
    assert identity is None
    assert "Invalid token" in detail
    assert "1234567890:WRONG" not in detail, "detail must not echo the token"


@requires_database
def test_telegram_invalid_token_via_api(
    client: TestClient, admin_auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("httpx.get", _fake_getme_invalid())
    secret = f"1234567890:INVALID_{uuid.uuid4().hex[:6]}"
    _save_token_via_api(client, admin_auth_headers, "telegram", secret)

    response = client.post(
        "/api/v1/bot-config/telegram/test", headers=admin_auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert secret not in json.dumps(body)

    # A failed test must not persist any identity.
    response = client.get("/api/v1/bot-config", headers=admin_auth_headers)
    telegram = next(item for item in response.json()["items"] if item["platform"] == "TELEGRAM")
    assert telegram["bot_username"] is None
    assert telegram["bot_name"] is None


# ===========================================================================
# 6. Bale connection success / failure (existing Bale API base URL)
# ===========================================================================


@requires_database
def test_bale_getme_success_uses_bale_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_urls: list[str] = []

    import httpx

    def fake_get(url: str, **kwargs):  # noqa: ANN001, ANN202
        seen_urls.append(url)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"id": 42, "is_bot": True, "first_name": "بله ربات", "username": "bale_bot"},
            },
        )

    monkeypatch.setattr("httpx.get", fake_get)

    ok, detail, identity = bot_config_service.test_connection("BALE", "BALE_TOKEN_1")
    assert ok is True
    assert "@bale_bot" in detail
    assert identity == {"bot_id": "42", "username": "bale_bot", "name": "بله ربات"}
    # Must hit Bale's own API base, not Telegram's.
    assert seen_urls and seen_urls[0].startswith("https://tapi.bale.ai/bot")


@requires_database
def test_bale_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.get", _fake_getme_invalid())
    ok, detail, identity = bot_config_service.test_connection("BALE", "BALE_WRONG")
    assert ok is False
    assert identity is None
    assert "BALE_WRONG" not in detail


# ===========================================================================
# 9. Bot runtime receives the persisted configuration
# ===========================================================================


@requires_database
def test_runtime_token_endpoint_returns_saved_token_when_enabled(
    client: TestClient, admin_auth_headers: dict[str, str]
) -> None:
    secret = f"111222333:AABB_{uuid.uuid4().hex[:6]}"
    _save_token_via_api(client, admin_auth_headers, "telegram", secret, enabled=True)

    # Without the runtime secret -> 401.
    response = client.get("/api/v1/bot-config/telegram/token")
    assert response.status_code == 401
    response = client.get(
        "/api/v1/bot-config/telegram/token",
        headers={"X-Bot-Runtime-Secret": "wrong-secret"},
    )
    assert response.status_code == 401

    # With the correct runtime secret -> plaintext token (bot startup path).
    response = client.get(
        "/api/v1/bot-config/telegram/token",
        headers={"X-Bot-Runtime-Secret": RUNTIME_SECRET},
    )
    assert response.status_code == 200
    assert response.json()["token"] == secret


@requires_database
def test_runtime_token_withheld_when_platform_disabled(
    client: TestClient, admin_auth_headers: dict[str, str]
) -> None:
    secret = f"111222333:CCDD_{uuid.uuid4().hex[:6]}"
    _save_token_via_api(client, admin_auth_headers, "telegram", secret, enabled=False)

    response = client.get(
        "/api/v1/bot-config/telegram/token",
        headers={"X-Bot-Runtime-Secret": RUNTIME_SECRET},
    )
    assert response.status_code == 200
    assert response.json()["token"] is None


# ===========================================================================
# 10. Configured-but-stopped bot is not shown as connected
# ===========================================================================


@requires_database
def test_configured_but_disabled_is_not_running() -> None:
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        bot_config_service.save_config(
            session, "TELEGRAM", enabled=False,
            token="1234567890:OFF", updated_by=system_user.id,
            secret_key="test-secret-key",
        )
        session.flush()

        assert bot_config_service.get_status(session, "TELEGRAM") == "DISABLED"
        assert bot_config_service.get_status(session, "TELEGRAM") != "RUNNING"
    finally:
        session.close()


@requires_database
def test_enabled_without_heartbeat_is_stopped_not_connected() -> None:
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        bot_config_service.save_config(
            session, "TELEGRAM", enabled=True,
            token="1234567890:ON", updated_by=system_user.id,
            secret_key="test-secret-key",
        )
        session.flush()

        # Token exists + enabled, but no process has heartbeated -> STOPPED,
        # never RUNNING/connected.
        assert bot_config_service.get_status(session, "TELEGRAM") == "STOPPED"

        # A real heartbeat flips it to RUNNING...
        bot_config_service.set_runtime_status(
            session, "TELEGRAM", status="RUNNING",
            updated_by=system_user.id, secret_key="test-secret-key",
        )
        session.flush()
        assert bot_config_service.get_status(session, "TELEGRAM") == "RUNNING"

        # ...and an ERROR report shows ERROR, not connected.
        bot_config_service.set_runtime_status(
            session, "TELEGRAM", status="ERROR",
            updated_by=system_user.id, secret_key="test-secret-key",
        )
        session.flush()
        assert bot_config_service.get_status(session, "TELEGRAM") == "ERROR"
    finally:
        session.close()


@requires_database
def test_status_not_configured_when_no_token() -> None:
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        bootstrap_service.ensure_bot_platforms(session, system_user.id)
        bot_config_service.set_runtime_status(
            session, "BALE", status="RUNNING",
            updated_by=system_user.id, secret_key="test-secret-key",
        )
        session.flush()
        # Even with a heartbeat, no token means NOT_CONFIGURED.
        assert bot_config_service.get_status(session, "BALE") == "NOT_CONFIGURED"
    finally:
        session.close()


# ===========================================================================
# 11. Secrets never appear in logs
# ===========================================================================


@requires_database
def test_secret_never_appears_in_logs(
    client: TestClient,
    admin_auth_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token lives inside the getMe URL, so httpx request logging would
    leak it.  Patch ``httpx.get`` with a real httpx Client + MockTransport so
    the full httpx logging machinery runs (and would print the URL) -- then
    assert the secret never reaches the captured logs.
    """
    import httpx as httpx_module

    secret = f"555666777:TOP_SECRET_{uuid.uuid4().hex[:6]}"

    def handler(request: httpx_module.Request) -> httpx_module.Response:
        return httpx_module.Response(
            401, request=request, json={"ok": False, "description": "Unauthorized"}
        )

    def fake_get(url: str, **kwargs):  # noqa: ANN001, ANN202
        with httpx_module.Client(transport=httpx_module.MockTransport(handler)) as client:
            return client.get(url, **kwargs)

    monkeypatch.setattr("httpx.get", fake_get)

    with caplog.at_level(logging.INFO):
        _save_token_via_api(client, admin_auth_headers, "telegram", secret, enabled=True)
        client.post("/api/v1/bot-config/telegram/test", headers=admin_auth_headers)
        client.get(
            "/api/v1/bot-config/telegram/token",
            headers={"X-Bot-Runtime-Secret": RUNTIME_SECRET},
        )
        client.post(
            "/api/v1/bot-config/telegram/runtime",
            json={"status": "RUNNING"},
            headers={"X-Bot-Runtime-Secret": RUNTIME_SECRET},
        )
        client.get("/api/v1/bot-config", headers=admin_auth_headers)

    assert secret not in caplog.text, "the raw token must never reach the logs"
    assert "TOP_SECRET" not in caplog.text


# ===========================================================================
# 4/5/6 extras: test endpoint returns 4xx-free useful error when unconfigured
# ===========================================================================


@requires_database
def test_test_connection_without_token_returns_useful_error(
    client: TestClient, admin_auth_headers: dict[str, str]
) -> None:
    response = client.post("/api/v1/bot-config/bale/test", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "No token configured" in body["detail"]


@requires_database
def test_unknown_platform_rejected(
    client: TestClient, admin_auth_headers: dict[str, str]
) -> None:
    response = client.put(
        "/api/v1/bot-config/slack",
        json={"enabled": True, "token": "x"},
        headers=admin_auth_headers,
    )
    assert response.status_code == 404