"""Tests proving credit note endpoint representative scope enforcement.

Covers:
1. Representative can read own credit note.
2. Representative cannot read another representative's credit note → 404.
3. Representative cannot create credit note against another rep's invoice → 404.
4. Representative cannot issue another rep's credit note → 404, no state change.
5. Representative cannot apply another rep's credit note → 404, no financial mutation.
6. Representative cannot void another rep's credit note → 404, no state change.
7. Admin/staff user retains unrestricted access.
8. Nonexistent credit note → 404 (same as out-of-scope).
9. Out-of-scope create creates no CreditNote.

All tests use real PostgreSQL (same skipif convention as other test files).
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from sqlalchemy import select

from database.models.credit_note import CreditNote
from database.models.customer import Customer
from database.models.invoice import Invoice
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping credit note scope tests",
)

CREDIT_NOTE_MANAGE = "CREDIT_NOTE_MANAGE"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _create_rep_user(session, system_user, rep, *, suffix: str):
    """Create a user linked to a representative, grant CREDIT_NOTE_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"cnscope_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    user.representative_id = rep.id
    session.flush()

    role_code = f"ROLE_CNSCOPE_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"CNScope {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=CREDIT_NOTE_MANAGE, name=CREDIT_NOTE_MANAGE, resource="credit_note", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=CREDIT_NOTE_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}, user


def _create_admin_user(session, system_user, *, suffix: str):
    """Create an admin user (no representative link), grant CREDIT_NOTE_MANAGE, return auth headers."""
    from security import create_access_token
    from app.core.config import get_settings

    settings = get_settings()
    username = f"cnscope_admin_{suffix}"
    password = "correct-horse-battery-staple"
    user = auth_service.create_user(
        session, username=username, email=f"{username}@example.invalid",
        password=password, created_by=system_user.id,
    )
    session.flush()

    role_code = f"ROLE_CNSCOPE_ADMIN_{suffix}"
    rbac_service.create_role(session, code=role_code, name=f"CNScopeAdmin {suffix}", created_by=system_user.id)
    try:
        rbac_service.create_permission(
            session, code=CREDIT_NOTE_MANAGE, name=CREDIT_NOTE_MANAGE, resource="credit_note", action="manage",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass
    rbac_service.grant_permission_to_role(session, role_code=role_code, permission_code=CREDIT_NOTE_MANAGE)
    rbac_service.assign_role(session, user_id=user.id, role_code=role_code, assigned_by=system_user.id)
    session.commit()

    token = create_access_token(
        subject=str(user.id), secret_key=settings.secret_key,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
    )
    return {"Authorization": f"Bearer {token}"}


def _create_issued_invoice(session, system_user, rep, customer, currency, warehouse,
                           product, price_history):
    """Create a fully shipped + issued invoice for the given representative."""
    from services import invoice_service, order_service

    order = order_service.create_order(
        session,
        customer_id=customer.id,
        representative_id=rep.id,
        currency_id=currency.id,
        order_type="LOCAL",
        fulfillment_mode="REP_LOCAL",
        sales_channel="OFFICE",
        lines=[
            order_service.OrderLineInput(
                product_id=product.id,
                fulfillment_warehouse_id=warehouse.id,
                price_history_id=price_history.id,
                qty_ordered=decimal.Decimal("3"),
                fulfillment_mode="REP_LOCAL",
            )
        ],
        created_by=system_user.id,
    )
    order_service.submit_order(session, order.id, actor_user_id=system_user.id)

    try:
        rbac_service.create_permission(
            session, code="ORDER_APPROVE", name="Approve", resource="order", action="approve",
            created_by=system_user.id,
        )
    except rbac_service.DuplicatePermissionCodeError:
        pass

    order_service.approve_order(session, order.id, actor_user_id=system_user.id)
    order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
    order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)

    order_line = list(order_service.list_order_lines(session, order.id))[0]
    order_service.ship_order(
        session, order.id,
        shipments=[order_service.ShipmentInput(order_line_id=order_line.id, quantity=decimal.Decimal("3"))],
        actor_user_id=system_user.id,
    )

    invoice = invoice_service.create_invoice_from_order(session, order_id=order.id, created_by=system_user.id)
    invoice_service.issue_invoice(session, invoice.id, actor_user_id=system_user.id)
    session.refresh(invoice)
    assert invoice.state == "ISSUED"
    return invoice


def _create_credit_note(session, system_user, invoice, reason_code):
    """Create a draft credit note against an invoice via the service layer."""
    from services import credit_note_service

    cn = credit_note_service.create_credit_note(
        session,
        invoice_id=invoice.id,
        reason_code_id=reason_code.id,
        lines=[{"description": "Test credit", "qty": decimal.Decimal("1"), "unit_price": decimal.Decimal("50.0000")}],
        created_by=system_user.id,
    )
    session.flush()
    session.refresh(cn)
    return cn


def _setup(client):
    """Create two representatives each with an issued invoice + credit note, plus two users and admin."""
    from fastapi.testclient import TestClient as _TC  # noqa: F811

    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)
        reason_code = bootstrap_service.ensure_default_reason_code(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]

        rep_a = Representative(
            code=f"REPA-CNS-{suffix}", person_name="Rep A", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        rep_b = Representative(
            code=f"REPB-CNS-{suffix}", person_name="Rep B", status="ACTIVE",
            created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([rep_a, rep_b])
        session.flush()

        product = Product(
            sku=f"SKU-CNS-{suffix}", name="CreditScope Product", base_uom_id=uom.id,
            status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"PL-CNS-{suffix}", price_type="RETAIL", currency_id=currency.id,
            owner_scope="GLOBAL", is_active=True, created_by=system_user.id, updated_by=system_user.id,
        )
        session.add(price_list)
        session.flush()

        price_history = PriceHistory(
            product_id=product.id, price_list_id=price_list.id, currency_id=currency.id,
            price_type="RETAIL", unit_price=decimal.Decimal("100.0000"), effective_from=_now(),
            created_by=system_user.id,
        )
        session.add(price_history)
        session.flush()

        from services import inventory_service
        inventory_service.post_transaction(
            session, product_id=product.id, warehouse_id=warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE", signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("50.0000"), currency_id=currency.id, actor_user_id=system_user.id,
        )
        session.flush()

        customer_a = Customer(
            code=f"CUSTA-CNS-{suffix}", name="Customer A", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        customer_b = Customer(
            code=f"CUSTB-CNS-{suffix}", name="Customer B", type="CORPORATE",
            currency_id=currency.id, status="ACTIVE", created_by=system_user.id, updated_by=system_user.id,
        )
        session.add_all([customer_a, customer_b])
        session.flush()

        # Create issued invoices for each representative
        invoice_a = _create_issued_invoice(
            session, system_user, rep_a, customer_a, currency, warehouse, product, price_history,
        )
        invoice_b = _create_issued_invoice(
            session, system_user, rep_b, customer_b, currency, warehouse, product, price_history,
        )

        # Create credit notes for each representative's invoice
        cn_a = _create_credit_note(session, system_user, invoice_a, reason_code)
        cn_b = _create_credit_note(session, system_user, invoice_b, reason_code)

        # Create users
        headers_a, user_a = _create_rep_user(session, system_user, rep_a, suffix=f"a_{suffix}")
        headers_b, user_b = _create_rep_user(session, system_user, rep_b, suffix=f"b_{suffix}")
        headers_admin = _create_admin_user(session, system_user, suffix=f"adm_{suffix}")

        session.commit()
    finally:
        session.close()

    return {
        "headers_a": headers_a,
        "headers_b": headers_b,
        "headers_admin": headers_admin,
        "cn_a_id": str(cn_a.id),
        "cn_b_id": str(cn_b.id),
        "invoice_a_id": str(invoice_a.id),
        "invoice_b_id": str(invoice_b.id),
        "reason_code_id": str(reason_code.id),
        "rep_a_id": str(rep_a.id),
        "rep_b_id": str(rep_b.id),
    }


@requires_database
class TestCreditNoteReadScope:
    """GET /credit-notes/{id} representative scope enforcement."""

    def test_representative_can_read_own_credit_note(self, client):
        """Representative can read their own credit note."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/credit-notes/{data['cn_a_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == data["cn_a_id"]

    def test_representative_cannot_read_other_rep_credit_note(self, client):
        """Representative cannot read another rep's credit note — 404."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/credit-notes/{data['cn_b_id']}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_nonexistent_credit_note_returns_404(self, client):
        """Nonexistent credit note returns 404 (same as out-of-scope)."""
        data = _setup(client)
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/v1/credit-notes/{fake_id}",
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

    def test_admin_can_read_any_credit_note(self, client):
        """Admin/staff user can read any credit note."""
        data = _setup(client)
        resp = client.get(
            f"/api/v1/credit-notes/{data['cn_b_id']}",
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == data["cn_b_id"]


@requires_database
class TestCreditNoteCreateScope:
    """POST /credit-notes creation scope enforcement."""

    def test_representative_can_create_for_own_invoice(self, client):
        """Representative can create credit note for their own invoice."""
        data = _setup(client)
        resp = client.post(
            "/api/v1/credit-notes",
            json={
                "invoice_id": data["invoice_a_id"],
                "reason_code_id": data["reason_code_id"],
                "lines": [{"description": "Own invoice", "qty": "1.0000", "unit_price": "10.0000"}],
            },
            headers=data["headers_a"],
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["state"] == "DRAFT"

    def test_representative_cannot_create_for_other_rep_invoice(self, client):
        """Representative cannot create credit note against another rep's invoice — 404."""
        data = _setup(client)
        # Count credit notes before.
        session = get_session_factory()()
        try:
            before = len(session.execute(
                select(CreditNote)
            ).scalars().all())
        finally:
            session.close()

        resp = client.post(
            "/api/v1/credit-notes",
            json={
                "invoice_id": data["invoice_b_id"],
                "reason_code_id": data["reason_code_id"],
                "lines": [{"description": "Cross-rep", "qty": "1.0000", "unit_price": "10.0000"}],
            },
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

        # Verify no credit note was created.
        session = get_session_factory()()
        try:
            after = len(session.execute(
                select(CreditNote)
            ).scalars().all())
            assert after == before, "No credit note should have been created for out-of-scope invoice"
        finally:
            session.close()


@requires_database
class TestCreditNoteWriteScope:
    """POST /credit-notes/{id}/* write endpoint scope enforcement."""

    def test_representative_cannot_issue_other_rep_credit_note(self, client):
        """Representative cannot issue another rep's credit note — 404, no state change."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/credit-notes/{data['cn_b_id']}/issue",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404
        # Verify credit note is still DRAFT.
        session = get_session_factory()()
        try:
            cn = session.get(CreditNote, uuid.UUID(data["cn_b_id"]))
            assert cn.state == "DRAFT"
        finally:
            session.close()

    def test_representative_cannot_apply_other_rep_credit_note(self, client):
        """Representative cannot apply another rep's credit note — 404, no financial mutation."""
        data = _setup(client)
        # First issue cn_b as admin so it can be applied.
        admin_resp = client.post(
            f"/api/v1/credit-notes/{data['cn_b_id']}/issue",
            json={},
            headers=data["headers_admin"],
        )
        assert admin_resp.status_code == 200, admin_resp.text

        # Attempt apply as rep_a (out of scope).
        resp = client.post(
            f"/api/v1/credit-notes/{data['cn_b_id']}/apply",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404

        # Verify credit note is still ISSUED (not APPLIED).
        session = get_session_factory()()
        try:
            cn = session.get(CreditNote, uuid.UUID(data["cn_b_id"]))
            assert cn.state == "ISSUED"
            # Verify the linked invoice is still ISSUED (not CLOSED_CORRECTED).
            invoice = session.get(Invoice, uuid.UUID(data["invoice_b_id"]))
            assert invoice.state == "ISSUED"
        finally:
            session.close()

    def test_representative_cannot_void_other_rep_credit_note(self, client):
        """Representative cannot void another rep's credit note — 404, no state change."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/credit-notes/{data['cn_b_id']}/void",
            json={},
            headers=data["headers_a"],
        )
        assert resp.status_code == 404
        # Verify credit note is still DRAFT.
        session = get_session_factory()()
        try:
            cn = session.get(CreditNote, uuid.UUID(data["cn_b_id"]))
            assert cn.state == "DRAFT"
        finally:
            session.close()

    def test_admin_can_issue_any_credit_note(self, client):
        """Admin/staff user can issue any credit note."""
        data = _setup(client)
        resp = client.post(
            f"/api/v1/credit-notes/{data['cn_b_id']}/issue",
            json={},
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "ISSUED"

    def test_admin_can_apply_any_credit_note(self, client):
        """Admin/staff user can apply any credit note."""
        data = _setup(client)
        # Issue first.
        client.post(
            f"/api/v1/credit-notes/{data['cn_b_id']}/issue",
            json={},
            headers=data["headers_admin"],
        )
        resp = client.post(
            f"/api/v1/credit-notes/{data['cn_b_id']}/apply",
            json={},
            headers=data["headers_admin"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "APPLIED"
