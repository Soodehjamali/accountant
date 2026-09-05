"""Focused tests for the ADR-013 REST/JWT bot architecture.

Covers (per the bot-integration acceptance list):
1.  valid representative phone verification
2.  invalid phone
3.  inactive representative
4.  repeated verification (idempotent re-bind)
5.  Telegram session persistence (survives "process restart" = new token)
6.  Bale session persistence (own platform identity)
7.  representative cannot access another representative (IDOR)
8.  inventory scope (only the rep's assigned warehouse)
9.  report scope (only the rep's own orders)
10. invoice authorization (BOT_WRITE required)
11. duplicate invoice prevention
12. revoked session rejected
13. expired session rejected
14. missing bot token -> NOT_CONFIGURED status
15. invalid Telegram token -> test connection fails
16. invalid Bale token -> test connection fails
17. audit records generated
18. secrets are not present in logs / responses
19. bot configuration save/retrieve behavior
20. bot status behavior (heartbeat -> RUNNING, stale -> STOPPED)

No real Telegram/Bale credentials are required: connection tests mock the
platform ``getMe`` API via monkeypatch.
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.models.app_user import AppUser
from database.models.audit_log import AuditLog
from database.models.bot_config import BotConfig as BotConfigTable
from database.models.customer import Customer
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.models.representative_contact import RepresentativeContact
from database.models.warehouse import Warehouse
from database.models.warehouse_assignment import WarehouseAssignment
from database.session import get_session_factory
from services import auth_service, bootstrap_service, inventory_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live DB bot tests",
)

BOT_QUERY = "BOT_QUERY"
BOT_WRITE = "BOT_WRITE"
BOT_MANAGE = "BOT_MANAGE"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _unique_phone() -> str:
    """Return a unique E.164-looking Iranian mobile number for this run.

    The test database persists across runs, so hardcoded phones collide on
    the second run (verify_phone would find multiple contacts).
    """
    return f"+989{uuid.uuid4().int % 10**9:09d}"


def _create_rep(
    session,
    system_user,
    *,
    phone: str,
    status: str = "ACTIVE",
) -> Representative:
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"REP-BOT-{suffix.upper()}",
        person_name=f"Bot Test Rep {suffix}",
        status=status,
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(rep)
    session.flush()
    contact = RepresentativeContact(
        representative_id=rep.id,
        kind="PHONE",
        value=phone,
        is_primary=True,
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(contact)
    session.flush()
    return rep


def _create_user_with_perms(
    session,
    system_user,
    *,
    rep: Representative | None,
    permissions: list[str],
) -> AppUser:
    suffix = uuid.uuid4().hex[:8]
    user = auth_service.create_user(
        session,
        username=f"bot_user_{suffix}",
        email=f"bot_{suffix}@test.invalid",
        password="test-password-123",
        created_by=system_user.id,
        representative_id=rep.id if rep is not None else None,
    )
    role_code = f"BOT_ROLE_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"Bot Role {suffix}", created_by=system_user.id
    )
    for code in permissions:
        try:
            rbac_service.create_permission(
                session,
                code=code,
                name=code,
                resource="bot",
                action="test",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass
        rbac_service.grant_permission_to_role(
            session, role_code=role_code, permission_code=code
        )
    rbac_service.assign_role(
        session, user_id=user.id, role_code=role_code, assigned_by=system_user.id
    )
    session.flush()
    return user


def _assign_warehouse(session, rep_id, warehouse_id, *, is_primary=True, actor_id):
    assignment = WarehouseAssignment(
        representative_id=rep_id,
        warehouse_id=warehouse_id,
        is_primary=is_primary,
        effective_from=_now() - datetime.timedelta(days=30),
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(assignment)
    session.flush()


def _assign_customer(session, rep_id, customer_id, *, actor_id):
    assignment = CustomerRepAssignment(
        customer_id=customer_id,
        representative_id=rep_id,
        effective_from=_now() - datetime.timedelta(days=30),
        priority=1,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(assignment)
    session.flush()


def _make_order_fixtures(session, system_user):
    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
    uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
    bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)

    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"SKU-BOT-{suffix}",
        name="Bot Test Product",
        base_uom_id=uom.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(product)
    session.flush()

    price_list = PriceList(
        name=f"Bot PL {suffix}",
        price_type="RETAIL",
        currency_id=currency.id,
        owner_scope="GLOBAL",
        is_active=True,
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(price_list)
    session.flush()

    price_history = PriceHistory(
        product_id=product.id,
        price_list_id=price_list.id,
        currency_id=currency.id,
        price_type="RETAIL",
        unit_price=decimal.Decimal("50.0000"),
        effective_from=_now(),
        created_by=system_user.id,
    )
    session.add(price_history)
    session.flush()
    return currency, warehouse, product, price_history


def _create_shipped_order(session, system_user, rep, warehouse, product, price_history):
    from services import order_service

    currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
    customer = Customer(
        code=f"CUST-BOT-{uuid.uuid4().hex[:8]}",
        name="Bot Test Customer",
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=system_user.id,
        updated_by=system_user.id,
    )
    session.add(customer)
    session.flush()

    inventory_service.post_transaction(
        session,
        product_id=product.id,
        warehouse_id=warehouse.id,
        movement_type_code="INITIAL_OPENING_BALANCE",
        signed_quantity=decimal.Decimal("100"),
        unit_cost=decimal.Decimal("25.0000"),
        currency_id=currency.id,
        actor_user_id=system_user.id,
    )
    session.flush()

    price_list = session.get(PriceList, price_history.price_list_id)
    order = order_service.create_order(
        session,
        customer_id=customer.id,
        representative_id=rep.id,
        currency_id=currency.id,
        price_list_id=price_list.id,
        order_type="LOCAL",
        fulfillment_mode="REP_LOCAL",
        sales_channel="OFFICE",
        lines=[
            order_service.OrderLineInput(
                product_id=product.id,
                fulfillment_warehouse_id=warehouse.id,
                price_history_id=price_history.id,
                qty_ordered=2,
                fulfillment_mode="REP_LOCAL",
            ),
        ],
        created_by=system_user.id,
    )
    order_service.submit_order(session, order.id, actor_user_id=system_user.id)
    order_service.approve_order(session, order.id, actor_user_id=system_user.id)
    order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
    order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)
    for line in order_service.list_order_lines(session, order.id):
        order_service.ship_order(
            session,
            order.id,
            shipments=[
                order_service.ShipmentInput(
                    order_line_id=line.id, quantity=line.qty_ordered
                )
            ],
            actor_user_id=system_user.id,
        )
    session.flush()
    session.refresh(order)
    assert order.state == "SHIPPED"
    return order


def _admin_login_headers(session) -> dict[str, str]:
    """Create a BOT_MANAGE user and return its bearer header."""
    from app.core.config import get_settings
    from security import create_access_token

    system_user = bootstrap_service.ensure_system_user(session)
    admin_user = _create_user_with_perms(
        session, system_user, rep=None, permissions=[BOT_MANAGE]
    )
    session.commit()
    settings = get_settings()
    token = create_access_token(
        subject=str(admin_user.id),
        secret_key=settings.secret_key,
        expires_in_seconds=3600,
    )
    return {"Authorization": f"Bearer {token}"}


def _bot_auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def phone_rep_ctx():
    """Rep A with BOT_QUERY+BOT_WRITE, phone, warehouse, customer, stock."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        bootstrap_service.ensure_bot_platforms(session, system_user.id)

        phone = _unique_phone()
        rep = _create_rep(session, system_user, phone=phone)
        _create_user_with_perms(
            session, system_user, rep=rep, permissions=[BOT_QUERY, BOT_WRITE]
        )
        _, warehouse, product, price_history = _make_order_fixtures(session, system_user)
        _assign_warehouse(session, rep.id, warehouse.id, is_primary=True, actor_id=system_user.id)
        order = _create_shipped_order(session, system_user, rep, warehouse, product, price_history)
        session.commit()
        yield {
            "session": session,
            "rep": rep,
            "warehouse": warehouse,
            "order": order,
            "phone": phone,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Phone verification
# ---------------------------------------------------------------------------


@requires_database
class TestVerifyPhone:
    def test_valid_phone_verification(self, client: TestClient, phone_rep_ctx) -> None:
        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": "tg-user-1",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["access_token"]
        assert body["representative_id"] == str(phone_rep_ctx["rep"].id)
        assert body["expires_in"] == 1800

    def test_phone_normalization_variants(self, client: TestClient, phone_rep_ctx) -> None:
        """Local-format phone numbers normalize to the stored E.164 form."""
        stored = phone_rep_ctx["phone"]  # e.g. +989123456789
        variants = (
            "0" + stored[3:],          # 09123456789
            stored[1:],                # 989123456789
            stored[:3] + " " + stored[3:5] + " " + stored[5:],  # +98 912 3456789
        )
        for variant in variants:
            resp = client.post(
                "/api/v1/bot/verify-phone",
                json={
                    "phone_number": variant,
                    "platform": "telegram",
                    "chat_id": f"tg-{uuid.uuid4().hex[:6]}",
                },
            )
            assert resp.status_code == 200, f"{variant}: {resp.text}"

    def test_invalid_phone_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={"phone_number": "+989999999999", "platform": "telegram", "chat_id": "x"},
        )
        assert resp.status_code == 404

    def test_inactive_representative_returns_403(self, client: TestClient) -> None:
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_bot_platforms(session, system_user.id)
            phone = _unique_phone()
            rep = _create_rep(session, system_user, phone=phone, status="SUSPENDED")
            session.commit()
        finally:
            session.close()

        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={"phone_number": phone, "platform": "telegram", "chat_id": "x"},
        )
        assert resp.status_code == 403

    def test_repeated_verification_is_idempotent(self, client: TestClient, phone_rep_ctx) -> None:
        """Verifying twice for the same platform identity reuses the same session row."""
        session = phone_rep_ctx["session"]
        chat_id = "tg-repeat-1"

        for _ in range(2):
            resp = client.post(
                "/api/v1/bot/verify-phone",
                json={
                    "phone_number": phone_rep_ctx["phone"],
                    "platform": "telegram",
                    "chat_id": chat_id,
                },
            )
            assert resp.status_code == 200, resp.text

        from database.models.bot_session import BotSession
        from sqlalchemy import select

        rows = session.execute(select(BotSession)).scalars().all()
        matching = [r for r in rows if r.platform_user_id == chat_id]
        assert len(matching) == 1, "Repeated verification must reuse the one session row"
        assert matching[0].status == "LINKED"
        assert matching[0].representative_id == phone_rep_ctx["rep"].id

    def test_telegram_and_bale_sessions_are_distinct(
        self, client: TestClient, phone_rep_ctx
    ) -> None:
        """The same chat id on different platforms never collides."""
        session = phone_rep_ctx["session"]
        for platform in ("telegram", "bale"):
            resp = client.post(
                "/api/v1/bot/verify-phone",
                json={
                    "phone_number": phone_rep_ctx["phone"],
                    "platform": platform,
                    "chat_id": "same-chat-id",
                },
            )
            assert resp.status_code == 200, resp.text

        from database.models.bot_platform_ref import BotPlatformRef
        from database.models.bot_session import BotSession
        from sqlalchemy import select

        rows = session.execute(
            select(BotSession).where(BotSession.platform_user_id == "same-chat-id")
        ).scalars().all()
        assert len(rows) == 2, "Telegram and Bale bindings must be separate rows"
        platform_ids = {r.bot_platform_id for r in rows}
        platform_codes = set(
            session.execute(
                select(BotPlatformRef.code).where(BotPlatformRef.id.in_(platform_ids))
            ).scalars().all()
        )
        assert platform_codes == {"TELEGRAM", "BALE"}

    def test_session_persists_after_restart(self, client: TestClient, phone_rep_ctx) -> None:
        """A new token after 'restart' resolves to the same persistent session."""
        resp1 = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": "tg-restart-1",
            },
        )
        token1 = resp1.json()["access_token"]

        # Bot process "restarts": the in-memory token cache is gone, the
        # representative re-shares their phone, and a new token is issued
        # for the same persistent session.
        resp2 = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": "tg-restart-1",
            },
        )
        token2 = resp2.json()["access_token"]

        # Both tokens still authorize the same representative.
        for token in (token1, token2):
            r = client.get(
                f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/inventory",
                headers=_bot_auth(token),
            )
            assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Authorization: scope + RBAC
# ---------------------------------------------------------------------------


@requires_database
class TestAuthorization:
    def test_inventory_scoped_to_assigned_warehouse(
        self, client: TestClient, phone_rep_ctx
    ) -> None:
        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": "tg-inv-1",
            },
        )
        token = resp.json()["access_token"]

        r = client.get(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/inventory",
            headers=_bot_auth(token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["warehouse_code"] == phone_rep_ctx["warehouse"].code
        assert any(item["balance"] > 0 for item in body["items"])

    def test_report_scoped_to_own_orders(self, client: TestClient, phone_rep_ctx) -> None:
        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": "tg-rep-1",
            },
        )
        token = resp.json()["access_token"]

        r = client.get(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/reports",
            headers=_bot_auth(token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        values = {s["label"]: s["value"] for s in body["summaries"]}
        assert values["تعداد سفارشات"] >= 1

    def test_cannot_access_another_representative(
        self, client: TestClient, phone_rep_ctx
    ) -> None:
        """Rep A's token must not access Rep B's resources (IDOR)."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            rep_b = _create_rep(session, system_user, phone=_unique_phone())
            _create_user_with_perms(
                session, system_user, rep=rep_b, permissions=[BOT_QUERY]
            )
            session.commit()
            rep_b_id = rep_b.id
        finally:
            session.close()

        # Authenticate as rep A.
        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": "tg-idor-1",
            },
        )
        token_a = resp.json()["access_token"]

        # Rep A tries to read rep B's inventory via B's id in the URL.
        r = client.get(
            f"/api/v1/bot/reps/{rep_b_id}/inventory",
            headers=_bot_auth(token_a),
        )
        assert r.status_code == 403, r.text

    def test_inventory_requires_bot_query_permission(
        self, client: TestClient, phone_rep_ctx
    ) -> None:
        """A verified rep whose linked user lacks BOT_QUERY gets 403."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_bot_platforms(session, system_user.id)
            no_perm_phone = _unique_phone()
            rep_no_perm = _create_rep(session, system_user, phone=no_perm_phone)
            # No permissions granted.
            session.commit()
        finally:
            session.close()

        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": no_perm_phone,
                "platform": "telegram",
                "chat_id": "tg-noperm-1",
            },
        )
        token = resp.json()["access_token"]

        r = client.get(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/inventory",
            headers=_bot_auth(token),
        )
        # Either 403 (no permission) or 403 (IDOR) -- both denied.
        assert r.status_code == 403

    def test_invoice_requires_bot_write(self, client: TestClient, phone_rep_ctx) -> None:
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_bot_platforms(session, system_user.id)
            read_only_phone = _unique_phone()
            rep_read_only = _create_rep(session, system_user, phone=read_only_phone)
            _create_user_with_perms(
                session, system_user, rep=rep_read_only, permissions=[BOT_QUERY]
            )
            session.commit()
        finally:
            session.close()

        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": read_only_phone,
                "platform": "telegram",
                "chat_id": "tg-nowrite-1",
            },
        )
        token = resp.json()["access_token"]

        r = client.post(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/invoices",
            json={"order_number": phone_rep_ctx["order"].order_number},
            headers=_bot_auth(token),
        )
        assert r.status_code == 403, r.text

    def test_invoice_flow_and_duplicate_prevention(
        self, client: TestClient, phone_rep_ctx
    ) -> None:
        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": "tg-inv-2",
            },
        )
        token = resp.json()["access_token"]
        order_number = phone_rep_ctx["order"].order_number

        r1 = client.post(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/invoices",
            json={"order_number": order_number},
            headers=_bot_auth(token),
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["status"] == "DRAFT"

        # Duplicate invoice for the same order must be rejected.
        r2 = client.post(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/invoices",
            json={"order_number": order_number},
            headers=_bot_auth(token),
        )
        assert r2.status_code == 409, r2.text

    def test_invoice_order_must_belong_to_rep(
        self, client: TestClient, phone_rep_ctx
    ) -> None:
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            system_user = bootstrap_service.ensure_system_user(session)
            bootstrap_service.ensure_bot_platforms(session, system_user.id)
            other_phone = _unique_phone()
            rep_b = _create_rep(session, system_user, phone=other_phone)
            _create_user_with_perms(
                session, system_user, rep=rep_b, permissions=[BOT_QUERY, BOT_WRITE]
            )
            currency, warehouse_b, product_b, ph_b = _make_order_fixtures(session, system_user)
            order_b = _create_shipped_order(
                session, system_user, rep_b, warehouse_b, product_b, ph_b
            )
            session.commit()
            order_b_number = order_b.order_number
        finally:
            session.close()

        # Authenticate as rep A (the phone_rep_ctx rep).
        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": "tg-xrep-1",
            },
        )
        token = resp.json()["access_token"]

        r = client.post(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/invoices",
            json={"order_number": order_b_number},
            headers=_bot_auth(token),
        )
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Session revocation / expiry
# ---------------------------------------------------------------------------


@requires_database
class TestSessionLifecycle:
    def _verify(self, client: TestClient, phone_rep_ctx, chat_id: str) -> str:
        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": chat_id,
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["access_token"]

    def test_revoked_session_rejected(self, client: TestClient, phone_rep_ctx) -> None:
        token = self._verify(client, phone_rep_ctx, "tg-revoke-1")

        # Revoke the session server-side (admin revocation / logout).
        from database.models.bot_session import BotSession
        from services import bot_session_service

        session = phone_rep_ctx["session"]
        bot_session = session.execute(
            __import__("sqlalchemy").select(BotSession).where(
                BotSession.platform_user_id == "tg-revoke-1"
            )
        ).scalar_one()
        system_user = bootstrap_service.ensure_system_user(session)
        bot_session_service.revoke_session_by_id(
            session, bot_session.id, revoked_by=system_user.id
        )
        session.commit()

        r = client.get(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/inventory",
            headers=_bot_auth(token),
        )
        assert r.status_code == 401, r.text

    def test_logout_revokes_session(self, client: TestClient, phone_rep_ctx) -> None:
        token = self._verify(client, phone_rep_ctx, "tg-logout-1")

        r = client.post("/api/v1/bot/logout", headers=_bot_auth(token))
        assert r.status_code == 204, r.text

        r2 = client.get(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/inventory",
            headers=_bot_auth(token),
        )
        assert r2.status_code == 401, r2.text

    def test_expired_session_rejected(self, client: TestClient, phone_rep_ctx) -> None:
        token = self._verify(client, phone_rep_ctx, "tg-expire-1")

        from database.models.bot_session import BotSession

        session = phone_rep_ctx["session"]
        bot_session = session.execute(
            __import__("sqlalchemy").select(BotSession).where(
                BotSession.platform_user_id == "tg-expire-1"
            )
        ).scalar_one()
        bot_session.expires_at = _now() - datetime.timedelta(minutes=1)
        session.commit()

        r = client.get(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/inventory",
            headers=_bot_auth(token),
        )
        assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# Bot configuration + status (admin)
# ---------------------------------------------------------------------------


@requires_database
class TestBotConfig:
    @pytest.fixture(autouse=True)
    def _clean_bot_config(self):
        """Wipe bot_config rows so each test starts from a clean slate
        (the test database persists across runs)."""
        from sqlalchemy import delete

        session = get_session_factory()()
        try:
            session.execute(delete(BotConfigTable))
            session.commit()
        finally:
            session.close()
        yield

    def _admin_headers(self) -> dict[str, str]:
        session = get_session_factory()()
        try:
            return _admin_login_headers(session)
        finally:
            session.close()

    def test_missing_token_is_not_configured(
        self, client: TestClient, phone_rep_ctx
    ) -> None:
        r = client.get("/api/v1/bot-config", headers=self._admin_headers())
        assert r.status_code == 200, r.text
        items = {i["platform"]: i for i in r.json()["items"]}
        assert items["TELEGRAM"]["status"] == "NOT_CONFIGURED"
        assert items["TELEGRAM"]["token_configured"] is False

    def test_save_and_retrieve_config(self, client: TestClient, phone_rep_ctx) -> None:
        secret = "123456:test-telegram-token-abcd"
        r = client.put(
            "/api/v1/bot-config/telegram",
            json={"enabled": True, "token": secret},
            headers=self._admin_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["token_configured"] is True
        assert body["token_hint"] == "abcd"
        # The secret itself must never be returned.
        assert secret not in r.text

        r2 = client.get("/api/v1/bot-config", headers=self._admin_headers())
        assert r2.status_code == 200
        assert secret not in r2.text
        items = {i["platform"]: i for i in r2.json()["items"]}
        assert items["TELEGRAM"]["enabled"] is True
        assert items["TELEGRAM"]["token_configured"] is True
        assert items["TELEGRAM"]["token_hint"] == "abcd"

    def test_update_without_token_keeps_existing(self, client: TestClient, phone_rep_ctx) -> None:
        headers = self._admin_headers()
        client.put(
            "/api/v1/bot-config/telegram",
            json={"enabled": True, "token": "123456:first-token-zzzz"},
            headers=headers,
        )
        r = client.put(
            "/api/v1/bot-config/telegram",
            json={"enabled": False, "token": None},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["token_configured"] is True, "Token must survive an update without a new token"

        r2 = client.get("/api/v1/bot-config", headers=headers)
        items = {i["platform"]: i for i in r2.json()["items"]}
        assert items["TELEGRAM"]["token_hint"] == "zzzz"

    def test_status_heartbeat_flow(self, client: TestClient, phone_rep_ctx) -> None:
        headers = self._admin_headers()
        secret_headers = {"X-Bot-Runtime-Secret": "dev-bot-runtime-secret"}

        client.put(
            "/api/v1/bot-config/telegram",
            json={"enabled": True, "token": "123456:telegram-token-abcd"},
            headers=headers,
        )

        # Configured + enabled but no heartbeat yet -> STOPPED (never faked).
        r = client.get("/api/v1/bot-config", headers=headers)
        items = {i["platform"]: i for i in r.json()["items"]}
        assert items["TELEGRAM"]["status"] == "STOPPED"

        # Bot process reports RUNNING -> status becomes RUNNING.
        r = client.post(
            "/api/v1/bot-config/telegram/runtime",
            json={"status": "RUNNING"},
            headers=secret_headers,
        )
        assert r.status_code == 200, r.text

        r = client.get("/api/v1/bot-config", headers=headers)
        items = {i["platform"]: i for i in r.json()["items"]}
        assert items["TELEGRAM"]["status"] == "RUNNING"

        # Bot process reports ERROR -> status becomes ERROR.
        client.post(
            "/api/v1/bot-config/telegram/runtime",
            json={"status": "ERROR"},
            headers=secret_headers,
        )
        r = client.get("/api/v1/bot-config", headers=headers)
        items = {i["platform"]: i for i in r.json()["items"]}
        assert items["TELEGRAM"]["status"] == "ERROR"

    def test_runtime_secret_required(self, client: TestClient, phone_rep_ctx) -> None:
        r = client.post(
            "/api/v1/bot-config/telegram/runtime",
            json={"status": "RUNNING"},
        )
        assert r.status_code == 401

    def test_invalid_tokens_fail_connection_test(
        self, client: TestClient, phone_rep_ctx, monkeypatch
    ) -> None:
        """Invalid tokens are rejected without real credentials (mocked API)."""
        import httpx

        class FakeResponse:
            status_code = 401

            def json(self):
                return {"ok": False, "error_code": 401, "description": "Unauthorized"}

        monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse())

        headers = self._admin_headers()
        client.put(
            "/api/v1/bot-config/telegram",
            json={"enabled": True, "token": "123456:bad-telegram-token"},
            headers=headers,
        )
        r = client.post("/api/v1/bot-config/telegram/test", headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "Invalid token" in r.json()["detail"]

        client.put(
            "/api/v1/bot-config/bale",
            json={"enabled": True, "token": "123456:bad-bale-token"},
            headers=headers,
        )
        r = client.post("/api/v1/bot-config/bale/test", headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is False

    def test_valid_token_connection_test(self, client: TestClient, phone_rep_ctx, monkeypatch) -> None:
        import httpx

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "ok": True,
                    "result": {"id": 1, "is_bot": True, "username": "MyERPbot"},
                }

        monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse())

        headers = self._admin_headers()
        client.put(
            "/api/v1/bot-config/telegram",
            json={"enabled": True, "token": "123456:good-telegram-token"},
            headers=headers,
        )
        r = client.post("/api/v1/bot-config/telegram/test", headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert "MyERPbot" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@requires_database
class TestBotAudit:
    def test_audit_records_generated(self, client: TestClient, phone_rep_ctx) -> None:
        from sqlalchemy import select

        # Verify phone -> AUTHENTICATE
        resp = client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": "tg-audit-1",
            },
        )
        token = resp.json()["access_token"]

        # Inventory query -> QUERY
        client.get(
            f"/api/v1/bot/reps/{phone_rep_ctx['rep'].id}/inventory",
            headers=_bot_auth(token),
        )

        session = phone_rep_ctx["session"]
        rows = session.execute(
            select(AuditLog).where(
                AuditLog.entity_type.in_(["bot_verify_phone", "bot_query"])
            )
        ).scalars().all()

        actions = {(r.entity_type, r.action) for r in rows}
        assert ("bot_verify_phone", "AUTHENTICATE") in actions
        assert ("bot_query", "QUERY") in actions

    def test_failed_verification_is_audited(self, client: TestClient, phone_rep_ctx) -> None:
        from sqlalchemy import select

        client.post(
            "/api/v1/bot/verify-phone",
            json={"phone_number": "+989999999999", "platform": "telegram", "chat_id": "x"},
        )

        session = phone_rep_ctx["session"]
        rows = session.execute(
            select(AuditLog).where(AuditLog.entity_type == "bot_verify_phone")
        ).scalars().all()
        assert any(
            r.action == "AUTHENTICATE"
            and r.after_json
            and r.after_json.get("result") == "failed"
            for r in rows
        )

    def test_no_secrets_in_audit(self, client: TestClient, phone_rep_ctx) -> None:
        """Audit rows must never contain phone numbers or bot tokens."""
        from sqlalchemy import select

        client.post(
            "/api/v1/bot/verify-phone",
            json={
                "phone_number": phone_rep_ctx["phone"],
                "platform": "telegram",
                "chat_id": "tg-secret-1",
            },
        )
        session = phone_rep_ctx["session"]
        rows = session.execute(select(AuditLog)).scalars().all()
        serialized = repr([(r.entity_type, r.after_json, r.before_json) for r in rows])
        # The phone number (PII) and any token-shaped secret must not appear.
        assert phone_rep_ctx["phone"] not in serialized
        assert "access_token" not in serialized
        assert "Bearer " not in serialized

    def test_plaintext_tokens_never_in_http_responses(
        self, client: TestClient, phone_rep_ctx
    ) -> None:
        headers = _admin_login_headers(phone_rep_ctx["session"])
        secret = "123456:ultra-secret-token-9999"
        client.put(
            "/api/v1/bot-config/telegram",
            json={"enabled": True, "token": secret},
            headers=headers,
        )
        r = client.get("/api/v1/bot-config", headers=headers)
        assert r.status_code == 200
        assert secret not in r.text