"""Focused tests for the /set-price bot command (Tier 2 — direct write).

Covers:
- BOT_WRITE required
- Missing arguments
- Invalid price (not a number, negative)
- Nonexistent product
- No DRAFT order found
- Product not on the order
- Successful price update (unit_price + line_total + order totals)
- Cross-representative isolation
- Audit recorded
- update_order_line_price service function directly

All tests use the real PostgreSQL database.
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.bot_platform_ref import BotPlatformRef
from database.models.customer import Customer
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service
from services import bot_session_service
from services.bot_command_service import (
    BOT_QUERY_PERMISSION,
    BOT_WRITE_PERMISSION,
    BotMessage,
    BotResponse,
    PermissionDeniedError,
    UnboundSessionError,
    process_message,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping /set-price tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_telegram_platform(session):
    existing = session.execute(
        select(BotPlatformRef).where(BotPlatformRef.code == "TELEGRAM")
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    su = bootstrap_service.ensure_system_user(session)
    p = BotPlatformRef(code="TELEGRAM", created_by=su.id, updated_by=su.id)
    session.add(p)
    session.flush()
    return p


def _create_representative(session, su, prefix="REP-SP"):
    suffix = uuid.uuid4().hex[:8]
    rep = Representative(
        code=f"{prefix}-{suffix.upper()}",
        person_name=f"SetPrice Rep {suffix}",
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(rep)
    session.flush()
    return rep


def _create_app_user(session, su, rep, prefix="sp_user"):
    suffix = uuid.uuid4().hex[:8]
    return auth_service.create_user(
        session,
        username=f"{prefix}_{suffix}",
        email=f"{prefix}_{suffix}@test.invalid",
        password="test-password-123",
        created_by=su.id,
        representative_id=rep.id,
    )


def _grant_permission(session, app_user, su, perm_code):
    suffix = uuid.uuid4().hex[:8]
    role_code = f"SP_{perm_code}_{suffix}"
    rbac_service.create_role(
        session, code=role_code, name=f"SP {perm_code} {suffix}",
        created_by=su.id,
    )
    try:
        rbac_service.create_permission(
            session, code=perm_code, name=f"Permission {perm_code}",
            resource="bot", action=perm_code.lower(), created_by=su.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(
        session, role_code=role_code, permission_code=perm_code,
    )
    rbac_service.assign_role(
        session, user_id=app_user.id, role_code=role_code,
        assigned_by=su.id,
    )


def _grant_bot_query(session, app_user, su):
    _grant_permission(session, app_user, su, BOT_QUERY_PERMISSION)


def _grant_bot_write(session, app_user, su):
    _grant_permission(session, app_user, su, BOT_WRITE_PERMISSION)


def _make_bound_session(session, su, *, platform_user_id):
    rep = _create_representative(session, su)
    user = _create_app_user(session, su, rep)
    _grant_bot_query(session, user, su)
    _grant_bot_write(session, user, su)
    _ensure_telegram_platform(session)
    token = bot_session_service.generate_binding_token(
        session, representative_id=rep.id, platform_code="TELEGRAM",
        created_by=su.id,
    )
    bot_session = bot_session_service.create_binding(
        session, binding_token=token, platform_code="TELEGRAM",
        platform_user_id=platform_user_id, linked_by=user.id,
    )
    return rep, user, bot_session


def _make_bound_session_no_write(session, su, *, platform_user_id):
    rep = _create_representative(session, su)
    user = _create_app_user(session, su, rep)
    _grant_bot_query(session, user, su)
    _ensure_telegram_platform(session)
    token = bot_session_service.generate_binding_token(
        session, representative_id=rep.id, platform_code="TELEGRAM",
        created_by=su.id,
    )
    bot_session = bot_session_service.create_binding(
        session, binding_token=token, platform_code="TELEGRAM",
        platform_user_id=platform_user_id, linked_by=user.id,
    )
    return rep, user, bot_session


def _create_product(session, su, prefix="SKU-SP"):
    suffix = uuid.uuid4().hex[:8]
    product = Product(
        sku=f"{prefix}-{suffix}",
        name=f"SetPrice Product {suffix}",
        base_uom_id=bootstrap_service.ensure_default_uom(
            session, actor_id=su.id,
        ).id,
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(product)
    session.flush()
    return product


def _create_customer(session, su, prefix="CUST-SP"):
    suffix = uuid.uuid4().hex[:8]
    currency = bootstrap_service.ensure_default_currency(
        session, actor_id=su.id,
    )
    customer = Customer(
        code=f"{prefix}-{suffix}",
        name=f"SetPrice Customer {suffix}",
        type="CORPORATE",
        currency_id=currency.id,
        status="ACTIVE",
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(customer)
    session.flush()
    return customer


def _create_price_list_and_history(session, su, product, unit_price="100.0000"):
    currency = bootstrap_service.ensure_default_currency(
        session, actor_id=su.id,
    )
    suffix = uuid.uuid4().hex[:8]
    price_list = PriceList(
        name=f"PL-SP-{suffix}",
        price_type="RETAIL",
        currency_id=currency.id,
        owner_scope="GLOBAL",
        is_active=True,
        created_by=su.id,
        updated_by=su.id,
    )
    session.add(price_list)
    session.flush()

    price_history = PriceHistory(
        product_id=product.id,
        price_list_id=price_list.id,
        currency_id=currency.id,
        price_type="RETAIL",
        unit_price=decimal.Decimal(unit_price),
        effective_from=datetime.datetime.now(datetime.timezone.utc),
        created_by=su.id,
    )
    session.add(price_history)
    session.flush()
    return price_list, price_history


def _create_draft_order(session, su, rep, customer, product, price_list, price_history, qty="5"):
    """Create a DRAFT order with one line for the given product."""
    warehouse = bootstrap_service.ensure_default_warehouse(
        session, actor_id=su.id,
    )
    bootstrap_service.ensure_movement_types(session, actor_id=su.id)

    from services import order_service

    order = order_service.create_order(
        session,
        customer_id=customer.id,
        representative_id=rep.id,
        currency_id=price_list.currency_id,
        price_list_id=price_list.id,
        order_type="LOCAL",
        fulfillment_mode="REP_LOCAL",
        sales_channel="OFFICE",
        lines=[
            order_service.OrderLineInput(
                product_id=product.id,
                fulfillment_warehouse_id=warehouse.id,
                price_history_id=price_history.id,
                qty_ordered=decimal.Decimal(qty),
                fulfillment_mode="REP_LOCAL",
            ),
        ],
        created_by=su.id,
    )
    return order


# ===========================================================================
# /set-price command tests
# ===========================================================================


@requires_database
class TestSetPriceSuccess:
    """Successful price override on a DRAFT order line."""

    def test_overrides_price_on_draft_order(self):
        """A valid /set-price command should update the unit_price."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )

            product = _create_product(session, su)
            price_list, price_history = _create_price_list_and_history(
                session, su, product, unit_price="100.0000",
            )
            customer = _create_customer(session, su)
            order = _create_draft_order(
                session, su, rep, customer, product, price_list,
                price_history, qty="5",
            )

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/set-price {product.sku} 250",
            )
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "250" in response.text
            assert "updated" in response.text.lower()

            # Verify the order line was actually updated.
            line = session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id)
            ).scalar_one()
            assert decimal.Decimal(line.unit_price) == decimal.Decimal("250")
            assert decimal.Decimal(line.line_total) == decimal.Decimal("1250")
        finally:
            session.close()

    def test_recalculates_order_totals(self):
        """Order grand_total should reflect the new price."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )

            product = _create_product(session, su)
            price_list, price_history = _create_price_list_and_history(
                session, su, product, unit_price="100.0000",
            )
            customer = _create_customer(session, su)
            order = _create_draft_order(
                session, su, rep, customer, product, price_list,
                price_history, qty="3",
            )

            # Original: 3 * 100 = 300
            assert decimal.Decimal(order.grand_total) == decimal.Decimal("300")

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/set-price {product.sku} 50",
            )
            process_message(session, message=msg)

            # Reload order and check totals.
            session.expire_all()
            reloaded = session.get(Order, order.id)
            # 3 * 50 = 150
            assert decimal.Decimal(reloaded.grand_total) == decimal.Decimal("150")
        finally:
            session.close()

    def test_price_zero_allowed(self):
        """A price of 0 should be accepted (non-negative)."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )

            product = _create_product(session, su)
            price_list, price_history = _create_price_list_and_history(
                session, su, product, unit_price="100.0000",
            )
            customer = _create_customer(session, su)
            order = _create_draft_order(
                session, su, rep, customer, product, price_list,
                price_history, qty="2",
            )

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/set-price {product.sku} 0",
            )
            response = process_message(session, message=msg)

            assert isinstance(response, BotResponse)
            assert "updated" in response.text.lower()
        finally:
            session.close()


@requires_database
class TestSetPriceValidation:
    """Validation and error cases."""

    def test_missing_arguments(self):
        """No args → usage hint."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/set-price",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_only_one_argument(self):
        """Only product SKU, no price → usage hint."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/set-price SKU-001",
            )
            response = process_message(session, message=msg)
            assert "Usage" in response.text
        finally:
            session.close()

    def test_invalid_price_not_a_number(self):
        """Non-numeric price → error."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/set-price SKU-001 abc",
            )
            response = process_message(session, message=msg)
            assert "Invalid price" in response.text
        finally:
            session.close()

    def test_negative_price_rejected(self):
        """Negative price → error."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/set-price SKU-001 -50",
            )
            response = process_message(session, message=msg)
            assert "non-negative" in response.text.lower()
        finally:
            session.close()

    def test_nonexistent_product(self):
        """Unknown product SKU → error."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/set-price SKU-NONEXIST 100",
            )
            response = process_message(session, message=msg)
            assert "not found" in response.text.lower()
        finally:
            session.close()

    def test_no_draft_order(self):
        """No DRAFT order for the representative → error."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid)

            product = _create_product(session, su)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/set-price {product.sku} 100",
            )
            response = process_message(session, message=msg)
            assert "No DRAFT order" in response.text
        finally:
            session.close()

    def test_product_not_on_order(self):
        """Product exists but is not on the DRAFT order → error."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )

            product1 = _create_product(session, su, "SKU-SP1")
            product2 = _create_product(session, su, "SKU-SP2")
            price_list, price_history = _create_price_list_and_history(
                session, su, product1, unit_price="100.0000",
            )
            # Also create price for product2 so it can be validated.
            _create_price_list_and_history(
                session, su, product2, unit_price="200.0000",
            )
            customer = _create_customer(session, su)
            _create_draft_order(
                session, su, rep, customer, product1, price_list,
                price_history, qty="5",
            )

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/set-price {product2.sku} 150",
            )
            response = process_message(session, message=msg)
            assert "not on order" in response.text.lower()
        finally:
            session.close()


@requires_database
class TestSetPricePermission:
    """Permission enforcement."""

    def test_requires_bot_write(self):
        """Without BOT_WRITE, the command is rejected."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            _make_bound_session_no_write(session, su, platform_user_id=puid)

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text="/set-price SKU-001 100",
            )
            with pytest.raises(PermissionDeniedError) as exc_info:
                process_message(session, message=msg)
            assert exc_info.value.permission_code == BOT_WRITE_PERMISSION
        finally:
            session.close()


@requires_database
class TestSetPriceScope:
    """Cross-representative isolation."""

    def test_cannot_override_price_on_other_rep_order(self):
        """Rep B must not be able to override prices on Rep A's order."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            # Rep A with a DRAFT order.
            puid_a = f"spa-{uuid.uuid4().hex[:6]}"
            rep_a, _, _ = _make_bound_session(
                session, su, platform_user_id=puid_a,
            )
            product = _create_product(session, su)
            price_list, price_history = _create_price_list_and_history(
                session, su, product, unit_price="100.0000",
            )
            customer = _create_customer(session, su)
            _create_draft_order(
                session, su, rep_a, customer, product, price_list,
                price_history, qty="5",
            )

            # Rep B (no DRAFT order of their own).
            puid_b = f"spb-{uuid.uuid4().hex[:6]}"
            _make_bound_session(session, su, platform_user_id=puid_b)

            msg_b = BotMessage(
                platform_user_id=puid_b, platform_code="TELEGRAM",
                text=f"/set-price {product.sku} 200",
            )
            response_b = process_message(session, message=msg_b)
            # Rep B has no DRAFT order → "No DRAFT order" message.
            assert "No DRAFT order" in response_b.text
        finally:
            session.close()


@requires_database
class TestSetPriceAudit:
    """Audit trail verification."""

    def test_price_update_recorded_in_audit(self):
        """Price override should write an audit_log row."""
        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)
            puid = f"sp-{uuid.uuid4().hex[:6]}"
            rep, user, _ = _make_bound_session(
                session, su, platform_user_id=puid,
            )

            product = _create_product(session, su)
            price_list, price_history = _create_price_list_and_history(
                session, su, product, unit_price="100.0000",
            )
            customer = _create_customer(session, su)
            order = _create_draft_order(
                session, su, rep, customer, product, price_list,
                price_history, qty="5",
            )

            msg = BotMessage(
                platform_user_id=puid, platform_code="TELEGRAM",
                text=f"/set-price {product.sku} 250",
            )
            process_message(session, message=msg)

            # Check audit_log for this order.
            from database.models.audit_log import AuditLog

            audit_rows = session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "order",
                    AuditLog.entity_id == order.id,
                    AuditLog.action == "UPDATE",
                )
            ).scalars().all()
            # Should have at least one UPDATE audit row.
            assert len(audit_rows) >= 1
            # The after payload should contain the price update info.
            last_audit = audit_rows[-1]
            assert last_audit.after_json is not None
            assert "update_line_price" in str(last_audit.after_json)
        finally:
            session.close()


# ===========================================================================
# Service-layer tests for update_order_line_price
# ===========================================================================


@requires_database
class TestUpdateOrderLinePriceService:
    """Direct tests for order_service.update_order_line_price()."""

    def test_service_updates_price_and_totals(self):
        """The service function should update unit_price, line_total,
        and order totals."""
        from services import order_service

        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            rep = _create_representative(session, su, "REP-SLS")
            user = _create_app_user(session, su, rep, "sls_user")
            product = _create_product(session, su, "SKU-SLS")
            price_list, price_history = _create_price_list_and_history(
                session, su, product, unit_price="100.0000",
            )
            customer = _create_customer(session, su, "CUST-SLS")
            order = _create_draft_order(
                session, su, rep, customer, product, price_list,
                price_history, qty="4",
            )

            line = session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id)
            ).scalar_one()

            updated_line = order_service.update_order_line_price(
                session,
                order_id=order.id,
                order_line_id=line.id,
                new_unit_price=decimal.Decimal("75.50"),
                actor_user_id=user.id,
            )

            assert decimal.Decimal(updated_line.unit_price) == decimal.Decimal("75.50")
            # 4 * 75.50 = 302.00
            assert decimal.Decimal(updated_line.line_total) == decimal.Decimal("302.00")

            session.expire_all()
            reloaded_order = session.get(Order, order.id)
            assert decimal.Decimal(reloaded_order.grand_total) == decimal.Decimal("302.00")
        finally:
            session.close()

    def test_service_rejects_non_draft_order(self):
        """Updating price on a non-DRAFT order raises OrderNotEditableError."""
        from services import order_service

        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            rep = _create_representative(session, su, "REP-NDE")
            user = _create_app_user(session, su, rep, "nde_user")
            product = _create_product(session, su, "SKU-NDE")
            price_list, price_history = _create_price_list_and_history(
                session, su, product, unit_price="100.0000",
            )
            customer = _create_customer(session, su, "CUST-NDE")
            order = _create_draft_order(
                session, su, rep, customer, product, price_list,
                price_history, qty="2",
            )

            # Submit the order (DRAFT → PENDING_APPROVAL).
            order_service.submit_order(
                session, order.id, actor_user_id=user.id,
            )

            line = session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id)
            ).scalar_one()

            with pytest.raises(order_service.OrderNotEditableError):
                order_service.update_order_line_price(
                    session,
                    order_id=order.id,
                    order_line_id=line.id,
                    new_unit_price=decimal.Decimal("50"),
                    actor_user_id=user.id,
                )
        finally:
            session.close()

    def test_service_rejects_negative_price(self):
        """Negative price raises ValueError."""
        from services import order_service

        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            rep = _create_representative(session, su, "REP-NPV")
            user = _create_app_user(session, su, rep, "npv_user")
            product = _create_product(session, su, "SKU-NPV")
            price_list, price_history = _create_price_list_and_history(
                session, su, product, unit_price="100.0000",
            )
            customer = _create_customer(session, su, "CUST-NPV")
            order = _create_draft_order(
                session, su, rep, customer, product, price_list,
                price_history, qty="1",
            )

            line = session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id)
            ).scalar_one()

            with pytest.raises(ValueError, match="non-negative"):
                order_service.update_order_line_price(
                    session,
                    order_id=order.id,
                    order_line_id=line.id,
                    new_unit_price=decimal.Decimal("-10"),
                    actor_user_id=user.id,
                )
        finally:
            session.close()

    def test_service_preserves_price_history_id(self):
        """The price_history_id should NOT be changed — it still records
        the original price provenance."""
        from services import order_service

        session = get_session_factory()()
        try:
            bootstrap_service.ensure_rbac_bootstrap(session)
            su = bootstrap_service.ensure_system_user(session)

            rep = _create_representative(session, su, "REP-PHI")
            user = _create_app_user(session, su, rep, "phi_user")
            product = _create_product(session, su, "SKU-PHI")
            price_list, price_history = _create_price_list_and_history(
                session, su, product, unit_price="100.0000",
            )
            customer = _create_customer(session, su, "CUST-PHI")
            order = _create_draft_order(
                session, su, rep, customer, product, price_list,
                price_history, qty="2",
            )

            line = session.execute(
                select(OrderLine).where(OrderLine.order_id == order.id)
            ).scalar_one()
            original_ph_id = line.price_history_id

            order_service.update_order_line_price(
                session,
                order_id=order.id,
                order_line_id=line.id,
                new_unit_price=decimal.Decimal("200"),
                actor_user_id=user.id,
            )

            session.expire_all()
            updated_line = session.get(OrderLine, line.id)
            assert updated_line.price_history_id == original_ph_id
            assert decimal.Decimal(updated_line.unit_price) == decimal.Decimal("200")
        finally:
            session.close()
