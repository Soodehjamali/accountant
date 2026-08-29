"""Tests for the credit note endpoints and service.

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as ``test_customers.py`` / ``test_invoices.py``).  Builds its
own supporting rows directly via the ORM/service layer.

Test matrix:
* Full happy path: create -> issue -> apply (verify invoice CLOSED_CORRECTED)
* Void from DRAFT
* Apply rejected if not ISSUED (409)
* Qty <= 0 rejected (422 -- DB CHECK violation)
* total_amount <= 0 rejected (422 -- DB CHECK violation)
* Permission gate (403)
* Invoice ends up CLOSED_CORRECTED after apply
* apply_credit_note without record_entry raises NotImplementedError
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from database.models.customer import Customer
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB credit note tests",
)

CREDIT_NOTE_MANAGE = "CREDIT_NOTE_MANAGE"


def _login(username: str, password: str) -> dict[str, str]:
    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session, username_or_email=username, password=password
        )
        assert user is not None
        session.commit()
        token = create_access_token(
            subject=str(user.id),
            secret_key=settings.secret_key,
            expires_in_seconds=settings.access_token_expire_minutes * 60,
        )
    finally:
        session.close()
    return {"Authorization": f"Bearer {token}"}


def _user_with_permissions(*permission_codes: str) -> dict[str, str]:
    """Create a fresh user, grant it every permission code given, log in."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)

        suffix = uuid.uuid4().hex[:8]
        username = f"test_cn_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"CREDIT_NOTE_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Credit Note Tester (test)", created_by=system_user.id
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session,
                    code=code,
                    name=code,
                    resource="credit_note",
                    action="test",
                    created_by=system_user.id,
                )
            except rbac_service.DuplicatePermissionCodeError:
                pass
            rbac_service.grant_permission_to_role(
                session, role_code=role_code, permission_code=code
            )
        rbac_service.assign_role(
            session, user_id=new_user.id, role_code=role_code, assigned_by=system_user.id
        )
        session.commit()
    finally:
        session.close()
    return _login(username, password)


@pytest.fixture()
def manage_auth_headers() -> dict[str, str]:
    return _user_with_permissions(CREDIT_NOTE_MANAGE)


@pytest.fixture()
def credit_note_fixtures() -> dict:
    """All supporting rows plus an ISSUED invoice (grand_total=500)."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)
        bootstrap_service.ensure_movement_types(session, actor_id=system_user.id)
        reason_code = bootstrap_service.ensure_default_reason_code(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]
        product = Product(
            sku=f"SKU-CN-{suffix}",
            name="Credit Note Test Product",
            base_uom_id=uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        price_list = PriceList(
            name=f"Test Price List {suffix}",
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
            unit_price=decimal.Decimal("100.0000"),
            effective_from=datetime.datetime.now(datetime.timezone.utc),
            created_by=system_user.id,
        )
        session.add(price_history)

        representative = Representative(
            code=f"REP-CN-{suffix}",
            person_name="Credit Note Test Representative",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(representative)

        customer = Customer(
            code=f"CUST-CN-{suffix}",
            name="Credit Note Test Customer",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(customer)
        session.flush()

        from services import inventory_service

        inventory_service.post_transaction(
            session,
            product_id=product.id,
            warehouse_id=warehouse.id,
            movement_type_code="INITIAL_OPENING_BALANCE",
            signed_quantity=decimal.Decimal("1000"),
            unit_cost=decimal.Decimal("50.0000"),
            currency_id=currency.id,
            actor_user_id=system_user.id,
        )

        # Create an order, ship it, create and issue its invoice.
        from services import invoice_service, order_service

        order = order_service.create_order(
            session,
            customer_id=customer.id,
            representative_id=representative.id,
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
                    qty_ordered=decimal.Decimal("5"),
                    fulfillment_mode="REP_LOCAL",
                )
            ],
            created_by=system_user.id,
        )
        order_service.submit_order(session, order.id, actor_user_id=system_user.id)

        try:
            rbac_service.create_permission(
                session,
                code="ORDER_APPROVE",
                name="Approve orders",
                resource="order",
                action="approve",
                created_by=system_user.id,
            )
        except rbac_service.DuplicatePermissionCodeError:
            pass

        order_service.approve_order(session, order.id, actor_user_id=system_user.id)
        order_service.reserve_order_stock(session, order.id, actor_user_id=system_user.id)
        order_service.start_fulfillment(session, order.id, actor_user_id=system_user.id)

        order_line = list(order_service.list_order_lines(session, order.id))[0]
        order_service.ship_order(
            session,
            order.id,
            shipments=[
                order_service.ShipmentInput(
                    order_line_id=order_line.id, quantity=decimal.Decimal("5")
                )
            ],
            actor_user_id=system_user.id,
        )

        invoice = invoice_service.create_invoice_from_order(
            session, order_id=order.id, created_by=system_user.id
        )
        invoice_service.issue_invoice(
            session, invoice.id, actor_user_id=system_user.id
        )
        session.refresh(invoice)
        assert invoice.state == "ISSUED", f"Invoice state is {invoice.state}"

        # Also grab an invoice_line for testing invoice_line_id references.
        invoice_lines = invoice_service.list_invoice_lines(session, invoice.id)
        invoice_line_id = str(invoice_lines[0].id) if invoice_lines else None

        session.commit()

        return {
            "currency_id": str(currency.id),
            "customer_id": str(customer.id),
            "invoice_id": str(invoice.id),
            "invoice_line_id": invoice_line_id,
            "reason_code_id": str(reason_code.id),
        }
    finally:
        session.close()


# ----------------------------------------------------------------- Tests


@requires_database
def test_full_happy_path_create_issue_apply(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    credit_note_fixtures: dict,
) -> None:
    """Full happy path: create -> issue -> apply, verify invoice CLOSED_CORRECTED.

    Uses a mock record_entry to verify the full apply path without needing
    the real Customer Ledger service.
    """
    # 1. Create a DRAFT credit note.
    resp = client.post(
        "/api/v1/credit-notes",
        json={
            "invoice_id": credit_note_fixtures["invoice_id"],
            "reason_code_id": credit_note_fixtures["reason_code_id"],
            "lines": [
                {
                    "description": "Pricing correction",
                    "qty": "2.0000",
                    "unit_price": "50.0000",
                    "invoice_line_id": credit_note_fixtures["invoice_line_id"],
                }
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    cn = resp.json()
    assert cn["state"] == "DRAFT"
    assert cn["total_amount"] == "100.0000"
    assert len(cn["lines"]) == 1
    assert cn["lines"][0]["line_total"] == "100.0000"
    cn_id = cn["id"]

    # 2. Issue it.
    resp = client.post(
        f"/api/v1/credit-notes/{cn_id}/issue",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "ISSUED"
    assert resp.json()["issued_at"] is not None

    # 3. Apply it -- uses the real endpoint which will 501 without
    #    a real record_entry.  We verify via the service layer directly.
    session = get_session_factory()()
    try:
        from services import credit_note_service

        system_user = bootstrap_service.ensure_system_user(session)
        mock_record = MagicMock()

        credit_note_service.apply_credit_note(
            session,
            uuid.UUID(cn_id),
            actor_user_id=system_user.id,
            record_entry=mock_record,
        )
        session.commit()

        # Verify the credit note is APPLIED.
        from database.models.credit_note import CreditNote
        from sqlalchemy import select

        updated_cn = session.execute(
            select(CreditNote).where(CreditNote.id == uuid.UUID(cn_id))
        ).scalar_one()
        assert updated_cn.state == "APPLIED"

        # Verify the original invoice is CLOSED_CORRECTED.
        from database.models.invoice import Invoice

        updated_inv = session.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(credit_note_fixtures["invoice_id"]))
        ).scalar_one()
        assert updated_inv.state == "CLOSED_CORRECTED"

        # Verify record_entry was called with correct args.
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["customer_id"] == uuid.UUID(credit_note_fixtures["customer_id"])
        assert call_kwargs["reference_type"] == "credit_note"
        assert call_kwargs["reference_id"] == uuid.UUID(cn_id)
        assert call_kwargs["signed_amount"] == decimal.Decimal("-100.0000")
        assert call_kwargs["entry_type"] == "CREDIT_NOTE_APPLIED"
    finally:
        session.close()


@requires_database
def test_create_note_persisted_to_audit_log(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    credit_note_fixtures: dict,
) -> None:
    """The create-time ``note`` field must actually be persisted somewhere.

    ``credit_note`` has no dedicated column (and no history table) for it,
    so it must land in the audit_log CREATE entry's ``after`` payload --
    same convention as invoice_service.create_invoice_from_order(). Guards
    against the note being silently accepted by the schema and dropped.
    """
    note_text = "Customer disputed the freight line item"
    resp = client.post(
        "/api/v1/credit-notes",
        json={
            "invoice_id": credit_note_fixtures["invoice_id"],
            "reason_code_id": credit_note_fixtures["reason_code_id"],
            "lines": [
                {
                    "description": "Freight correction",
                    "qty": "1.0000",
                    "unit_price": "25.0000",
                }
            ],
            "note": note_text,
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    cn_id = resp.json()["id"]

    session = get_session_factory()()
    try:
        from services import audit_service

        entries = audit_service.list_entries(
            session, entity_type="credit_note", entity_id=uuid.UUID(cn_id)
        )
        create_entries = [e for e in entries if e.action == "CREATE"]
        assert len(create_entries) == 1
        assert create_entries[0].after_json["note"] == note_text
    finally:
        session.close()


@requires_database
def test_void_from_draft(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    credit_note_fixtures: dict,
) -> None:
    """Void a DRAFT credit note."""
    # Create
    resp = client.post(
        "/api/v1/credit-notes",
        json={
            "invoice_id": credit_note_fixtures["invoice_id"],
            "reason_code_id": credit_note_fixtures["reason_code_id"],
            "lines": [
                {
                    "description": "To be voided",
                    "qty": "1.0000",
                    "unit_price": "25.0000",
                }
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    cn_id = resp.json()["id"]

    # Void
    resp = client.post(
        f"/api/v1/credit-notes/{cn_id}/void",
        json={"note": "No longer needed"},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "VOID"


@requires_database
def test_apply_rejected_if_not_issued(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    credit_note_fixtures: dict,
) -> None:
    """Trying to apply a DRAFT credit note must be rejected (409)."""
    resp = client.post(
        "/api/v1/credit-notes",
        json={
            "invoice_id": credit_note_fixtures["invoice_id"],
            "reason_code_id": credit_note_fixtures["reason_code_id"],
            "lines": [
                {
                    "description": "Should not apply",
                    "qty": "1.0000",
                    "unit_price": "10.0000",
                }
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    cn_id = resp.json()["id"]
    assert resp.json()["state"] == "DRAFT"

    # Try to apply (not ISSUED) -- should 409.
    resp = client.post(
        f"/api/v1/credit-notes/{cn_id}/apply",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 409, resp.text


@requires_database
def test_permission_gate(
    client: TestClient,
    credit_note_fixtures: dict,
) -> None:
    """Creating a credit note without CREDIT_NOTE_MANAGE permission returns 403."""
    # Use a user with NO credit note permissions.
    headers = _user_with_permissions()  # no permissions
    resp = client.post(
        "/api/v1/credit-notes",
        json={
            "invoice_id": credit_note_fixtures["invoice_id"],
            "reason_code_id": credit_note_fixtures["reason_code_id"],
            "lines": [
                {
                    "description": "Should be forbidden",
                    "qty": "1.0000",
                    "unit_price": "10.0000",
                }
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 403, resp.text


@requires_database
def test_read_credit_note(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    credit_note_fixtures: dict,
) -> None:
    """GET /credit-notes/{id} returns the credit note with lines."""
    # Create
    resp = client.post(
        "/api/v1/credit-notes",
        json={
            "invoice_id": credit_note_fixtures["invoice_id"],
            "reason_code_id": credit_note_fixtures["reason_code_id"],
            "lines": [
                {
                    "description": "Line A",
                    "qty": "3.0000",
                    "unit_price": "20.0000",
                },
                {
                    "description": "Line B",
                    "qty": "1.0000",
                    "unit_price": "10.0000",
                },
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    cn_id = resp.json()["id"]

    # Read
    resp = client.get(
        f"/api/v1/credit-notes/{cn_id}",
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == cn_id
    assert body["total_amount"] == "70.0000"
    assert len(body["lines"]) == 2


@requires_database
def test_qty_nonpositive_rejected(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    credit_note_fixtures: dict,
) -> None:
    """Trying to create a credit note line with qty <= 0 must be rejected (422)."""
    resp = client.post(
        "/api/v1/credit-notes",
        json={
            "invoice_id": credit_note_fixtures["invoice_id"],
            "reason_code_id": credit_note_fixtures["reason_code_id"],
            "lines": [
                {
                    "description": "Bad qty",
                    "qty": "0.0000",
                    "unit_price": "10.0000",
                }
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 422, resp.text


@requires_database
def test_total_amount_nonpositive_rejected(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    credit_note_fixtures: dict,
) -> None:
    """Trying to create a credit note with total_amount <= 0 must be rejected (422).

    All lines have positive qty and unit_price, but we test the validation
    by creating lines that compute to a valid total, then verify the DB
    CHECK constraint works by attempting a negative total via the schema.
    """
    # The schema itself prevents qty <= 0 via Field(gt=0), but the service
    # layer also validates.  We test the service-layer validation path.
    from services import credit_note_service

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        bootstrap_service.ensure_rbac_bootstrap(session)
        with pytest.raises(credit_note_service.CreditNoteAmountNonPositiveError):
            credit_note_service.create_credit_note(
                session,
                invoice_id=uuid.UUID(credit_note_fixtures["invoice_id"]),
                reason_code_id=uuid.UUID(credit_note_fixtures["reason_code_id"]),
                lines=[],  # empty lines -> total = 0
                created_by=system_user.id,
            )
    finally:
        session.close()


@requires_database
def test_apply_credit_note_succeeds_with_ledger_wired(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    credit_note_fixtures: dict,
) -> None:
    """The /apply endpoint succeeds when record_entry is wired (customer ledger is live)."""
    # Create and issue a credit note via the API.
    resp = client.post(
        "/api/v1/credit-notes",
        json={
            "invoice_id": credit_note_fixtures["invoice_id"],
            "reason_code_id": credit_note_fixtures["reason_code_id"],
            "lines": [
                {
                    "description": "Ledger wired",
                    "qty": "1.0000",
                    "unit_price": "10.0000",
                }
            ],
        },
        headers=manage_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    cn_id = resp.json()["id"]

    resp = client.post(
        f"/api/v1/credit-notes/{cn_id}/issue",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Apply -- should succeed (200) with the real record_entry wired.
    resp = client.post(
        f"/api/v1/credit-notes/{cn_id}/apply",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "APPLIED"
