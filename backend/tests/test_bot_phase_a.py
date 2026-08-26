"""Phase A tests for bot skeleton.

Tests cover:
- bot_session_service: binding token lifecycle, session CRUD, message logging
- bot_command_service: message normalization, command routing, RBAC gate
- telegram_adapter/normalizer: Telegram Update -> BotMessage
- telegram_adapter/formatter: BotResponse -> Telegram sendMessage params
- All tests use the real PostgreSQL database (no mocks for DB operations).
- No Telegram API calls are mocked -- the adapter is tested at the
  normalizer/formatter level only (the polling loop is not tested in Phase A).

Follows the project's existing test conventions (see test_customers.py,
test_orders.py, etc.): get_session_factory(), bootstrap_service, auth_service,
rbac_service, system_user for created_by/updated_by.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select

from database.models.bot_binding_token import BotBindingToken
from database.models.bot_message_log import BotMessageLog
from database.models.bot_platform_ref import BotPlatformRef
from database.models.bot_session import BotSession
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service
from services import bot_session_service
from services.bot_command_service import (
    BOT_QUERY_PERMISSION,
    BotMessage,
    BotResponse,
    COMMAND_REGISTRY,
    UnboundSessionError,
    _parse_command,
    process_message,
)
from telegram_adapter.formatter import format_response
from telegram_adapter.normalizer import normalize_update


requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live DB bot tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_telegram_platform(session) -> BotPlatformRef:
    """Return the seeded TELEGRAM platform, creating if absent."""
    existing = session.execute(
        select(BotPlatformRef).where(BotPlatformRef.code == "TELEGRAM")
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    # Need a real system_user for UAC created_by/updated_by.
    system_user = bootstrap_service.ensure_system_user(session)
    p = BotPlatformRef(code="TELEGRAM", created_by=system_user.id, updated_by=system_user.id)
    session.add(p)
    session.flush()
    return p


def _create_representative(session, system_user) -> Representative:
    """Create a minimal representative for testing."""
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-{suffix.upper()}",
        person_name=f"Test Rep {suffix}",
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_app_user_with_rep(session, system_user):
    """Create an AppUser + Representative pair, return (representative, app_user).
    Uses auth_service.create_user() for proper FK compliance.
    """
    suffix = uuid.uuid4().hex[:8]
    rep = _create_representative(session, system_user)

    username = f"bot_user_{suffix}"
    app_user = auth_service.create_user(
        session,
        username=username,
        email=f"{username}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id,
    )
    return rep, app_user


def _grant_bot_query(session, app_user, system_user) -> None:
    """Grant BOT_QUERY permission to an app_user via a test role."""
    suffix = uuid.uuid4().hex[:8]
    role_code = f"BOT_QUERY_TEST_{suffix}"

    rbac_service.create_role(
        session, code=role_code, name=f"Bot Query Tester {suffix}",
        created_by=system_user.id,
    )
    # Permission may already exist from a prior test run.
    try:
        rbac_service.create_permission(
            session,
            code=BOT_QUERY_PERMISSION,
            name="Query data via bot",
            resource="bot",
            action="query",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=BOT_QUERY_PERMISSION)
    rbac_service.assign_role(session, user_id=app_user.id, role_code=role_code, assigned_by=system_user.id)


def _make_bound_session(session, system_user, *, platform_user_id: str):
    """Create a representative + user + bound session, return (rep, user, bot_session)."""
    rep, app_user = _create_app_user_with_rep(session, system_user)
    _grant_bot_query(session, app_user, system_user)
    _ensure_telegram_platform(session)

    token = bot_session_service.generate_binding_token(
        session, representative_id=rep.id, platform_code="TELEGRAM",
        created_by=system_user.id,
    )
    bot_session = bot_session_service.create_binding(
        session,
        binding_token=token,
        platform_code="TELEGRAM",
        platform_user_id=platform_user_id,
        linked_by=app_user.id,
    )
    return rep, app_user, bot_session


# ===========================================================================
# Tests: bot_session_service
# ===========================================================================


@requires_database
class TestBindingTokenLifecycle:
    """Binding tokens: generate, consume, expiry, reuse, persistence."""

    def test_generate_and_consume(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            _ensure_telegram_platform(session)

            token = bot_session_service.generate_binding_token(
                session, representative_id=rep.id, platform_code="TELEGRAM",
                created_by=system_user.id,
            )
            assert isinstance(token, str)
            assert len(token) > 10

            # Consume succeeds.
            rep_id, platform_id = bot_session_service._consume_binding_token(
                session, token, consumed_by=system_user.id,
            )
            assert rep_id == rep.id
            assert platform_id is not None

            # Second consume fails (single-use).
            with pytest.raises(bot_session_service.InvalidBindingTokenError):
                bot_session_service._consume_binding_token(
                    session, token, consumed_by=system_user.id,
                )
        finally:
            session.close()

    def test_invalid_token_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            with pytest.raises(bot_session_service.InvalidBindingTokenError):
                bot_session_service._consume_binding_token(
                    session, "totally-fake-token", consumed_by=system_user.id,
                )
        finally:
            session.close()

    def test_expired_token_rejected(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            _ensure_telegram_platform(session)

            token = bot_session_service.generate_binding_token(
                session, representative_id=rep.id, platform_code="TELEGRAM",
                created_by=system_user.id,
            )
            # Manually expire the token in the DB.
            token_hash = bot_session_service._hash_token(token)
            row = session.execute(
                select(BotBindingToken).where(BotBindingToken.token_hash == token_hash)
            ).scalar_one()
            from datetime import datetime, timezone
            row.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
            session.flush()

            with pytest.raises(bot_session_service.InvalidBindingTokenError):
                bot_session_service._consume_binding_token(
                    session, token, consumed_by=system_user.id,
                )
        finally:
            session.close()

    def test_nonexistent_representative_raises(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            _ensure_telegram_platform(session)
            with pytest.raises(bot_session_service.RepresentativeNotFoundError):
                bot_session_service.generate_binding_token(
                    session, representative_id=uuid.uuid4(), platform_code="TELEGRAM",
                    created_by=system_user.id,
                )
        finally:
            session.close()

    def test_nonexistent_platform_raises(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            with pytest.raises(bot_session_service.PlatformNotFoundError):
                bot_session_service.generate_binding_token(
                    session, representative_id=rep.id, platform_code="NOPE",
                    created_by=system_user.id,
                )
        finally:
            session.close()

    def test_token_persisted_in_db(self):
        """Generate a token and verify the BotBindingToken row exists in the DB."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            _ensure_telegram_platform(session)

            token = bot_session_service.generate_binding_token(
                session, representative_id=rep.id, platform_code="TELEGRAM",
                created_by=system_user.id,
            )
            token_hash = bot_session_service._hash_token(token)

            row = session.execute(
                select(BotBindingToken).where(BotBindingToken.token_hash == token_hash)
            ).scalar_one_or_none()
            assert row is not None, "Binding token row should exist in DB"
            assert row.representative_id == rep.id
            assert row.consumed_at is None, "Token should not be consumed yet"
        finally:
            session.close()

    def test_consumed_token_has_timestamps(self):
        """After consumption, consumed_at and consumed_by are set."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            _ensure_telegram_platform(session)

            token = bot_session_service.generate_binding_token(
                session, representative_id=rep.id, platform_code="TELEGRAM",
                created_by=system_user.id,
            )
            token_hash = bot_session_service._hash_token(token)

            bot_session_service._consume_binding_token(
                session, token, consumed_by=system_user.id,
            )

            row = session.execute(
                select(BotBindingToken).where(BotBindingToken.token_hash == token_hash)
            ).scalar_one()
            assert row.consumed_at is not None
            assert row.consumed_by == system_user.id
        finally:
            session.close()

    def test_raw_token_not_stored(self):
        """The raw token is never stored in the DB -- only its hash."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            _ensure_telegram_platform(session)

            token = bot_session_service.generate_binding_token(
                session, representative_id=rep.id, platform_code="TELEGRAM",
                created_by=system_user.id,
            )
            # The raw token should not appear in any BotBindingToken row.
            all_tokens = session.execute(select(BotBindingToken)).scalars().all()
            for t in all_tokens:
                assert t.token_hash != token, "Raw token must not be stored as token_hash"
        finally:
            session.close()


@requires_database
class TestSessionCRUD:
    """Session create, resolve, revoke."""

    def test_create_binding_and_resolve(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"tg-{uuid.uuid4().hex[:6]}"
            rep, app_user, bot_session = _make_bound_session(session, system_user, platform_user_id=puid)

            assert bot_session.status == "LINKED"
            assert bot_session.representative_id == rep.id

            # Resolve succeeds.
            resolved = bot_session_service.resolve_session(
                session, platform_code="TELEGRAM", platform_user_id=puid
            )
            assert resolved is not None
            assert resolved.id == bot_session.id
        finally:
            session.close()

    def test_unlinked_session_returns_none(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            resolved = bot_session_service.resolve_session(
                session, platform_code="TELEGRAM", platform_user_id="99999"
            )
            assert resolved is None
        finally:
            session.close()

    def test_get_or_create_session_raises_when_unlinked(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            with pytest.raises(bot_session_service.SessionNotLinkedError):
                bot_session_service.get_or_create_session(
                    session, platform_code="TELEGRAM", platform_user_id="99999"
                )
        finally:
            session.close()

    def test_revoke_session(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"revoke-{uuid.uuid4().hex[:6]}"
            rep, app_user, bot_session = _make_bound_session(session, system_user, platform_user_id=puid)

            revoked = bot_session_service.revoke_session(
                session, platform_code="TELEGRAM", platform_user_id=puid,
                revoked_by=app_user.id,
            )
            assert revoked.status == "REVOKED"

            # After revocation, resolve returns None.
            assert bot_session_service.resolve_session(
                session, platform_code="TELEGRAM", platform_user_id=puid,
            ) is None
        finally:
            session.close()

    def test_already_linked_raises(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"double-{uuid.uuid4().hex[:6]}"
            rep, app_user, _ = _make_bound_session(session, system_user, platform_user_id=puid)

            token2 = bot_session_service.generate_binding_token(
                session, representative_id=rep.id, platform_code="TELEGRAM",
                created_by=system_user.id,
            )
            with pytest.raises(bot_session_service.SessionAlreadyLinkedError):
                bot_session_service.create_binding(
                    session,
                    binding_token=token2,
                    platform_code="TELEGRAM",
                    platform_user_id=puid,
                    linked_by=app_user.id,
                )
        finally:
            session.close()


@requires_database
class TestMessageLogging:
    """Inbound/outbound message logging."""

    def test_log_inbound_and_outbound(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            puid = f"log-{uuid.uuid4().hex[:6]}"
            rep, app_user, bot_session = _make_bound_session(session, system_user, platform_user_id=puid)

            inbound = bot_session_service.log_inbound(
                session,
                bot_session_id=bot_session.id,
                platform_code="TELEGRAM",
                raw_payload={"text": "/orders"},
                command_parsed="orders",
            )
            assert inbound.direction == "INBOUND"

            outbound = bot_session_service.log_outbound(
                session,
                bot_session_id=bot_session.id,
                platform_code="TELEGRAM",
                raw_payload={"text": "Here are your orders..."},
                command_parsed="orders",
            )
            assert outbound.direction == "OUTBOUND"

            logs = session.execute(
                select(BotMessageLog).where(BotMessageLog.bot_session_id == bot_session.id)
            ).scalars().all()
            assert len(logs) == 2
        finally:
            session.close()


# ===========================================================================
# Tests: bot_command_service
# ===========================================================================


class TestParseCommand:
    """Command text parsing (no DB needed)."""

    def test_parse_slash_command(self):
        cmd, args = _parse_command("/orders")
        assert cmd == "orders"
        assert args == ""

    def test_parse_slash_command_with_args(self):
        cmd, args = _parse_command("/order abc-123")
        assert cmd == "order"
        assert args == "abc-123"

    def test_parse_plain_text(self):
        cmd, args = _parse_command("hello world")
        assert cmd == ""
        assert args == "hello world"

    def test_parse_empty(self):
        cmd, args = _parse_command("")
        assert cmd == ""
        assert args == ""


class TestCommandRegistry:
    """All expected v1 commands are registered (no DB needed)."""

    def test_expected_commands_registered(self):
        expected = {"me", "balance", "orders", "order", "inventory", "customers"}
        assert expected.issubset(set(COMMAND_REGISTRY.keys()))

    def test_commands_have_permission(self):
        for name, handler in COMMAND_REGISTRY.items():
            perm = getattr(handler, "_required_permission", None)
            assert perm is not None, f"Command '{name}' missing _required_permission"


@requires_database
class TestProcessMessage:
    """Core message processing: session resolution, RBAC, dispatch."""

    def test_unbound_user_rejected(self):
        session = get_session_factory()()
        try:
            _ensure_telegram_platform(session)
            msg = BotMessage(
                platform_user_id="99999",
                platform_code="TELEGRAM",
                text="/orders",
            )
            with pytest.raises(UnboundSessionError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_bound_user_with_permission_succeeds(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            uid = f"cmd-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(session, system_user, platform_user_id=uid)

            msg = BotMessage(
                platform_user_id=uid,
                platform_code="TELEGRAM",
                text="/me",
            )
            response = process_message(session, message=msg)
            assert isinstance(response, BotResponse)
            assert rep.person_name in response.text
        finally:
            session.close()

    def test_bound_user_without_permission_denied(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            suffix = uuid.uuid4().hex[:8]
            app_user = auth_service.create_user(
                session,
                username=f"noperm_{suffix}",
                email=f"noperm_{suffix}@test.invalid",
                password="test-password-123",
                created_by=system_user.id,
                representative_id=rep.id,
            )
            # Do NOT grant BOT_QUERY.
            _ensure_telegram_platform(session)

            puid = f"noperm-{uuid.uuid4().hex[:6]}"
            token = bot_session_service.generate_binding_token(
                session, representative_id=rep.id, platform_code="TELEGRAM",
                created_by=system_user.id,
            )
            bot_session_service.create_binding(
                session,
                binding_token=token,
                platform_code="TELEGRAM",
                platform_user_id=puid,
                linked_by=app_user.id,
            )

            from services.bot_command_service import PermissionDeniedError
            msg = BotMessage(
                platform_user_id=puid,
                platform_code="TELEGRAM",
                text="/me",
            )
            with pytest.raises(PermissionDeniedError):
                process_message(session, message=msg)
        finally:
            session.close()

    def test_unknown_command_returns_help_hint(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            uid = f"unk-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=uid)

            msg = BotMessage(
                platform_user_id=uid,
                platform_code="TELEGRAM",
                text="/nonexistent",
            )
            response = process_message(session, message=msg)
            assert "Unknown command" in response.text
            assert "/help" in response.text
        finally:
            session.close()

    def test_start_command(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            uid = f"start-{uuid.uuid4().hex[:6]}"
            rep, _, _ = _make_bound_session(session, system_user, platform_user_id=uid)

            msg = BotMessage(
                platform_user_id=uid,
                platform_code="TELEGRAM",
                text="/start",
            )
            response = process_message(session, message=msg)
            assert "Welcome" in response.text
        finally:
            session.close()

    def test_order_without_args_returns_usage(self):
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            uid = f"usage-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, system_user, platform_user_id=uid)

            msg = BotMessage(
                platform_user_id=uid,
                platform_code="TELEGRAM",
                text="/order",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()


# ===========================================================================
# Regression tests: explicit user <-> representative mapping
# ===========================================================================


@requires_database
class TestUserRepresentativeMapping:
    """Verify that bot_command_service resolves AppUser via
    representative_id (FK), NOT by assuming AppUser.id == representative_id.
    """

    def test_user_lookup_uses_representative_id_not_pk(self):
        """AppUser.representative_id points to the Representative; AppUser.id is
        a different PK.  The bot must find the user by the FK, not by PK.
        """
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)
            suffix = uuid.uuid4().hex[:8]
            app_user = auth_service.create_user(
                session,
                username=f"repmap_{suffix}",
                email=f"repmap_{suffix}@test.invalid",
                password="test-password-123",
                created_by=system_user.id,
                representative_id=rep.id,
            )

            # Confirm the IDs are different (the whole point of this test).
            assert app_user.id != rep.id, (
                "AppUser.id must differ from Representative.id for this regression test"
            )

            # The bot's default user-lookup must find the user via representative_id.
            from services.bot_command_service import _find_user_by_representative
            found = _find_user_by_representative(session, rep.id)
            assert found is not None
            assert found.id == app_user.id
            assert found.representative_id == rep.id
        finally:
            session.close()

    def test_user_lookup_returns_none_when_no_user_linked(self):
        """A representative with no AppUser should yield None, not raise."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep = _create_representative(session, system_user)

            from services.bot_command_service import _find_user_by_representative
            found = _find_user_by_representative(session, rep.id)
            assert found is None
        finally:
            session.close()

    def test_process_message_resolves_correct_user_for_rep(self):
        """End-to-end: process_message uses the correct AppUser for the
        session's representative, even when multiple users exist.
        """
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)

            # Create two rep+user pairs.
            rep1, user1 = _create_app_user_with_rep(session, system_user)
            rep2, user2 = _create_app_user_with_rep(session, system_user)
            _grant_bot_query(session, user1, system_user)
            _grant_bot_query(session, user2, system_user)
            _ensure_telegram_platform(session)

            # Bind rep1 to a Telegram user.
            puid1 = f"map1-{uuid.uuid4().hex[:6]}"
            token1 = bot_session_service.generate_binding_token(
                session, representative_id=rep1.id, platform_code="TELEGRAM",
                created_by=system_user.id,
            )
            bot_session_service.create_binding(
                session, binding_token=token1, platform_code="TELEGRAM",
                platform_user_id=puid1, linked_by=user1.id,
            )

            msg = BotMessage(platform_user_id=puid1, platform_code="TELEGRAM", text="/me")
            response = process_message(session, message=msg)

            # Response should contain rep1's name, NOT rep2's.
            assert rep1.person_name in response.text
            assert rep2.person_name not in response.text
        finally:
            session.close()


# ===========================================================================
# Tests: telegram_adapter normalizer
# ===========================================================================


class TestNormalizeUpdate:
    """Telegram Update -> BotMessage normalization (no DB needed)."""

    def test_normalize_text_message(self):
        update = {
            "update_id": 100,
            "message": {
                "message_id": 200,
                "from": {"id": 789, "first_name": "Alice", "username": "alice123"},
                "chat": {"id": 789, "type": "private"},
                "date": 1234567890,
                "text": "/orders",
            },
        }
        msg = normalize_update(update)
        assert msg is not None
        assert msg.platform_user_id == "789"
        assert msg.platform_code == "TELEGRAM"
        assert msg.text == "/orders"
        assert msg.metadata["telegram_chat_id"] == "789"
        assert msg.metadata["sender_first_name"] == "Alice"
        assert msg.metadata["sender_username"] == "alice123"

    def test_non_message_update_returns_none(self):
        update = {"update_id": 101, "callback_query": {"data": "click"}}
        assert normalize_update(update) is None

    def test_no_text_returns_none(self):
        update = {
            "update_id": 102,
            "message": {
                "message_id": 201,
                "from": {"id": 789},
                "chat": {"id": 789},
                "date": 1234567890,
            },
        }
        assert normalize_update(update) is None

    def test_no_sender_id_returns_none(self):
        update = {
            "update_id": 103,
            "message": {
                "message_id": 202,
                "from": {},
                "chat": {"id": 789},
                "date": 1234567890,
                "text": "/help",
            },
        }
        assert normalize_update(update) is None


# ===========================================================================
# Tests: telegram_adapter formatter
# ===========================================================================


class TestFormatResponse:
    """BotResponse -> Telegram sendMessage params (no DB needed)."""

    def test_format_simple_response(self):
        response = BotResponse(text="Hello!")
        params = format_response(response, chat_id="123")
        assert params == {"chat_id": "123", "text": "Hello!"}

    def test_format_with_parse_mode(self):
        response = BotResponse(text="*Bold*", parse_mode="Markdown")
        params = format_response(response, chat_id="123")
        assert params["parse_mode"] == "Markdown"

    def test_format_with_reply_to(self):
        response = BotResponse(text="Reply", reply_to_message_id="456")
        params = format_response(response, chat_id="123")
        assert params["reply_to_message_id"] == "456"

    def test_format_none_parse_mode_omitted(self):
        response = BotResponse(text="Plain", parse_mode=None)
        params = format_response(response, chat_id="123")
        assert "parse_mode" not in params
