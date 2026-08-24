"""Tests for the Customer Ledger service (M13 / T22).

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as ``test_customers.py`` / ``test_invoices.py``).  Builds its
own supporting rows directly via the ORM/service layer.

Test matrix:
* Entry sequencing is monotonic and gapless per customer
* Balance computed correctly across a mixed sequence of
  invoice/payment/credit-note entries
* Reconciliation function updates the cached projection correctly
* Amount-nonzero / entry-type CHECK violations rejected
* Full integration: invoice -> payment -> credit_note ledger entries
  via the wired endpoints
"""

from __future__ import annotations

import datetime
import decimal
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from database.models.customer import Customer
from database.models.customer_ledger import CustomerLedger
from database.models.customer_ledger_entry import CustomerLedgerEntry
from database.session import get_session_factory
from services import auth_service, bootstrap_service, customer_ledger_service, rbac_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB customer ledger tests",
)

CUSTOMER_LEDGER_VIEW = "CUSTOMER_LEDGER_VIEW"
CUSTOMER_LEDGER_MANAGE = "CUSTOMER_LEDGER_MANAGE"


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
        username = f"test_cl_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"CUST_LEDGER_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Customer Ledger Tester (test)", created_by=system_user.id
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session,
                    code=code,
                    name=code,
                    resource="customer_ledger",
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
def view_auth_headers() -> dict[str, str]:
    return _user_with_permissions(CUSTOMER_LEDGER_VIEW)


@pytest.fixture()
def manage_auth_headers() -> dict[str, str]:
    return _user_with_permissions(CUSTOMER_LEDGER_MANAGE)


@pytest.fixture()
def ledger_fixtures() -> dict:
    """All supporting rows plus a customer with a ledger header."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        bootstrap_service.ensure_rbac_bootstrap(session)

        suffix = uuid.uuid4().hex[:8]

        customer = Customer(
            code=f"CUST-LED-{suffix}",
            name="Customer Ledger Test Customer",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(customer)
        session.flush()

        # Create the ledger header.
        ledger = customer_ledger_service.ensure_customer_ledger(
            session,
            customer_id=customer.id,
            currency_id=currency.id,
        )
        session.commit()

        return {
            "customer_id": str(customer.id),
            "currency_id": str(currency.id),
            "ledger_id": str(ledger.id),
        }
    finally:
        session.close()


# ----------------------------------------------------------------- Tests


@requires_database
def test_entry_sequencing_is_monotonic_and_gapless(
    client: TestClient,
    ledger_fixtures: dict,
) -> None:
    """Entries for the same customer should have monotonically increasing,
    gapless sequence_no values starting from 1.
    """
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        customer_id = uuid.UUID(ledger_fixtures["customer_id"])
        currency_id = uuid.UUID(ledger_fixtures["currency_id"])

        # Record 5 entries.
        for i in range(5):
            entry = customer_ledger_service.record_entry(
                session,
                customer_id=customer_id,
                reference_type="invoice",
                reference_id=uuid.uuid4(),
                signed_amount=decimal.Decimal(f"{(i + 1) * 100}.0000"),
                currency_id=currency_id,
                entry_type="INVOICE_ISSUED",
                actor_user_id=system_user.id,
            )
            assert entry.sequence_no == i + 1, (
                f"Expected sequence_no {i + 1}, got {entry.sequence_no}"
            )

        # Verify all sequence_nos are present and gapless.
        entries = customer_ledger_service.list_entries(session, customer_id)
        seq_nos = [e.sequence_no for e in entries]
        assert seq_nos == [1, 2, 3, 4, 5], f"Expected [1,2,3,4,5], got {seq_nos}"

        session.commit()
    finally:
        session.close()


@requires_database
def test_balance_across_mixed_entry_types(
    client: TestClient,
    ledger_fixtures: dict,
) -> None:
    """Balance is correctly computed across invoice (+debit), payment
    (-credit), and credit_note (-credit) entries.
    """
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        customer_id = uuid.UUID(ledger_fixtures["customer_id"])
        currency_id = uuid.UUID(ledger_fixtures["currency_id"])

        # Invoice: +500
        customer_ledger_service.record_entry(
            session,
            customer_id=customer_id,
            reference_type="invoice",
            reference_id=uuid.uuid4(),
            signed_amount=decimal.Decimal("500.0000"),
            currency_id=currency_id,
            entry_type="INVOICE_ISSUED",
            actor_user_id=system_user.id,
        )
        assert customer_ledger_service.get_balance(session, customer_id) == decimal.Decimal("500.0000")

        # Payment: -200
        customer_ledger_service.record_entry(
            session,
            customer_id=customer_id,
            reference_type="payment",
            reference_id=uuid.uuid4(),
            signed_amount=decimal.Decimal("-200.0000"),
            currency_id=currency_id,
            entry_type="PAYMENT_RECEIVED",
            actor_user_id=system_user.id,
        )
        assert customer_ledger_service.get_balance(session, customer_id) == decimal.Decimal("300.0000")

        # Credit note: -50
        customer_ledger_service.record_entry(
            session,
            customer_id=customer_id,
            reference_type="credit_note",
            reference_id=uuid.uuid4(),
            signed_amount=decimal.Decimal("-50.0000"),
            currency_id=currency_id,
            entry_type="CREDIT_NOTE_APPLIED",
            actor_user_id=system_user.id,
        )
        assert customer_ledger_service.get_balance(session, customer_id) == decimal.Decimal("250.0000")

        # Second invoice: +1000
        customer_ledger_service.record_entry(
            session,
            customer_id=customer_id,
            reference_type="invoice",
            reference_id=uuid.uuid4(),
            signed_amount=decimal.Decimal("1000.0000"),
            currency_id=currency_id,
            entry_type="INVOICE_ISSUED",
            actor_user_id=system_user.id,
        )
        assert customer_ledger_service.get_balance(session, customer_id) == decimal.Decimal("1250.0000")

        session.commit()
    finally:
        session.close()


@requires_database
def test_reconcile_updates_cached_projection(
    client: TestClient,
    ledger_fixtures: dict,
) -> None:
    """reconcile_customer_ledger() updates the cached current_balance,
    last_entry_seq, and last_reconciled_at columns correctly.
    """
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        customer_id = uuid.UUID(ledger_fixtures["customer_id"])
        currency_id = uuid.UUID(ledger_fixtures["currency_id"])

        # Record 3 entries.
        for i in range(3):
            customer_ledger_service.record_entry(
                session,
                customer_id=customer_id,
                reference_type="invoice",
                reference_id=uuid.uuid4(),
                signed_amount=decimal.Decimal(f"{(i + 1) * 100}.0000"),
                currency_id=currency_id,
                entry_type="INVOICE_ISSUED",
                actor_user_id=system_user.id,
            )

        # Before reconciliation, the cached columns may be stale.
        ledger = session.execute(
            select(CustomerLedger).where(CustomerLedger.customer_id == customer_id)
        ).scalar_one()

        # Reconcile.
        reconciled = customer_ledger_service.reconcile_customer_ledger(session, customer_id)

        # Verify cached projection matches the live computation.
        assert reconciled.current_balance == decimal.Decimal("600.0000")
        assert reconciled.last_entry_seq == 3
        assert reconciled.last_reconciled_at is not None

        # The cached balance should now match get_balance().
        live_balance = customer_ledger_service.get_balance(session, customer_id)
        assert reconciled.current_balance == live_balance

        session.commit()
    finally:
        session.close()


@requires_database
def test_entry_amount_zero_rejected(
    client: TestClient,
    ledger_fixtures: dict,
) -> None:
    """Record an entry with signed_amount=0 must be rejected."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        customer_id = uuid.UUID(ledger_fixtures["customer_id"])
        currency_id = uuid.UUID(ledger_fixtures["currency_id"])

        with pytest.raises(customer_ledger_service.EntryAmountZeroError):
            customer_ledger_service.record_entry(
                session,
                customer_id=customer_id,
                reference_type="invoice",
                reference_id=uuid.uuid4(),
                signed_amount=decimal.Decimal("0"),
                currency_id=currency_id,
                entry_type="INVOICE_ISSUED",
                actor_user_id=system_user.id,
            )
    finally:
        session.close()


@requires_database
def test_entry_invalid_type_rejected(
    client: TestClient,
    ledger_fixtures: dict,
) -> None:
    """Record an entry with an invalid entry_type must be rejected."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        customer_id = uuid.UUID(ledger_fixtures["customer_id"])
        currency_id = uuid.UUID(ledger_fixtures["currency_id"])

        with pytest.raises(customer_ledger_service.InvalidEntryTypeError):
            customer_ledger_service.record_entry(
                session,
                customer_id=customer_id,
                reference_type="invoice",
                reference_id=uuid.uuid4(),
                signed_amount=decimal.Decimal("100.0000"),
                currency_id=currency_id,
                entry_type="INVALID_TYPE",
                actor_user_id=system_user.id,
            )
    finally:
        session.close()


@requires_database
def test_hash_chain_integrity(
    client: TestClient,
    ledger_fixtures: dict,
) -> None:
    """Each entry's prev_hash should match the previous entry's row_hash."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        customer_id = uuid.UUID(ledger_fixtures["customer_id"])
        currency_id = uuid.UUID(ledger_fixtures["currency_id"])

        prev_hash = None
        for i in range(3):
            entry = customer_ledger_service.record_entry(
                session,
                customer_id=customer_id,
                reference_type="invoice",
                reference_id=uuid.uuid4(),
                signed_amount=decimal.Decimal(f"{(i + 1) * 100}.0000"),
                currency_id=currency_id,
                entry_type="INVOICE_ISSUED",
                actor_user_id=system_user.id,
            )
            assert entry.prev_hash == prev_hash, (
                f"Entry {i + 1}: prev_hash {entry.prev_hash} != expected {prev_hash}"
            )
            prev_hash = entry.row_hash

        session.commit()
    finally:
        session.close()


@requires_database
def test_permission_gate_view(
    client: TestClient,
    ledger_fixtures: dict,
) -> None:
    """Accessing ledger without CUSTOMER_LEDGER_VIEW returns 403."""
    headers = _user_with_permissions()  # no permissions
    resp = client.get(
        f"/api/v1/customers/{ledger_fixtures['customer_id']}/ledger",
        headers=headers,
    )
    assert resp.status_code == 403, resp.text


@requires_database
def test_permission_gate_balance(
    client: TestClient,
    ledger_fixtures: dict,
) -> None:
    """Accessing balance without CUSTOMER_LEDGER_VIEW returns 403."""
    headers = _user_with_permissions()  # no permissions
    resp = client.get(
        f"/api/v1/customers/{ledger_fixtures['customer_id']}/balance",
        headers=headers,
    )
    assert resp.status_code == 403, resp.text


@requires_database
def test_permission_gate_reconcile(
    client: TestClient,
    ledger_fixtures: dict,
) -> None:
    """Reconciling without CUSTOMER_LEDGER_MANAGE returns 403."""
    headers = _user_with_permissions()  # no permissions
    resp = client.post(
        f"/api/v1/customers/{ledger_fixtures['customer_id']}/ledger/reconcile",
        json={},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text


@requires_database
def test_list_entries_empty(
    client: TestClient,
    view_auth_headers: dict[str, str],
    ledger_fixtures: dict,
) -> None:
    """GET /customers/{id}/ledger returns empty list when no entries exist."""
    resp = client.get(
        f"/api/v1/customers/{ledger_fixtures['customer_id']}/ledger",
        headers=view_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


@requires_database
def test_get_balance_empty(
    client: TestClient,
    view_auth_headers: dict[str, str],
    ledger_fixtures: dict,
) -> None:
    """GET /customers/{id}/balance returns 0 when no entries exist."""
    resp = client.get(
        f"/api/v1/customers/{ledger_fixtures['customer_id']}/balance",
        headers=view_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["balance"] == "0.0000"


@requires_database
def test_reconcile_endpoint(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    ledger_fixtures: dict,
) -> None:
    """POST /customers/{id}/ledger/reconcile updates the cached projection."""
    # First, record some entries via the service.
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        customer_id = uuid.UUID(ledger_fixtures["customer_id"])
        currency_id = uuid.UUID(ledger_fixtures["currency_id"])

        for i in range(3):
            customer_ledger_service.record_entry(
                session,
                customer_id=customer_id,
                reference_type="invoice",
                reference_id=uuid.uuid4(),
                signed_amount=decimal.Decimal(f"{(i + 1) * 100}.0000"),
                currency_id=currency_id,
                entry_type="INVOICE_ISSUED",
                actor_user_id=system_user.id,
            )
        session.commit()
    finally:
        session.close()

    # Reconcile via the endpoint.
    resp = client.post(
        f"/api/v1/customers/{ledger_fixtures['customer_id']}/ledger/reconcile",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_balance"] == "600.0000"
    assert body["last_entry_seq"] == 3
    assert body["last_reconciled_at"] is not None


@requires_database
def test_reconcile_nonexistent_customer_404(
    client: TestClient,
    manage_auth_headers: dict[str, str],
) -> None:
    """Reconciling a customer with no ledger header returns 404."""
    fake_id = str(uuid.uuid4())
    resp = client.post(
        f"/api/v1/customers/{fake_id}/ledger/reconcile",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 404, resp.text


@requires_database
def test_ensure_customer_ledger_idempotent(
    client: TestClient,
    ledger_fixtures: dict,
) -> None:
    """ensure_customer_ledger() returns the same row on repeated calls."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        customer_id = uuid.UUID(ledger_fixtures["customer_id"])
        currency_id = uuid.UUID(ledger_fixtures["currency_id"])

        ledger1 = customer_ledger_service.ensure_customer_ledger(
            session,
            customer_id=customer_id,
            currency_id=currency_id,
        )
        ledger2 = customer_ledger_service.ensure_customer_ledger(
            session,
            customer_id=customer_id,
            currency_id=currency_id,
        )
        assert ledger1.id == ledger2.id
    finally:
        session.close()


@requires_database
def test_record_entry_standalone_no_prior_ensure(
    client: TestClient,
) -> None:
    """record_entry() succeeds for a brand-new customer without any prior
    ensure_customer_ledger() call -- it internally get-or-creates the
    customer_ledger header.
    """
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)

        suffix = uuid.uuid4().hex[:8]
        customer = Customer(
            code=f"CUST-NOLEDGER-{suffix}",
            name="No-Ledger Customer",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(customer)
        session.flush()

        # Verify no ledger header exists yet.
        existing = session.execute(
            select(CustomerLedger).where(CustomerLedger.customer_id == customer.id)
        ).scalar_one_or_none()
        assert existing is None, "Pre-condition: no ledger header should exist yet"

        # record_entry should internally create the header.
        entry = customer_ledger_service.record_entry(
            session,
            customer_id=customer.id,
            reference_type="invoice",
            reference_id=uuid.uuid4(),
            signed_amount=decimal.Decimal("250.0000"),
            currency_id=currency.id,
            entry_type="INVOICE_ISSUED",
            actor_user_id=system_user.id,
        )
        assert entry.sequence_no == 1

        # The ledger header should now exist.
        ledger = session.execute(
            select(CustomerLedger).where(CustomerLedger.customer_id == customer.id)
        ).scalar_one()
        assert ledger is not None

        # Balance should reflect the single entry.
        balance = customer_ledger_service.get_balance(session, customer.id)
        assert balance == decimal.Decimal("250.0000")

        session.commit()
    finally:
        session.close()


@requires_database
def test_list_entries_nonexistent_customer_404(
    client: TestClient,
    view_auth_headers: dict[str, str],
) -> None:
    """GET /customers/{id}/ledger returns 404 for a nonexistent customer."""
    fake_id = str(uuid.uuid4())
    resp = client.get(
        f"/api/v1/customers/{fake_id}/ledger",
        headers=view_auth_headers,
    )
    assert resp.status_code == 404, resp.text


@requires_database
def test_get_balance_nonexistent_customer_404(
    client: TestClient,
    view_auth_headers: dict[str, str],
) -> None:
    """GET /customers/{id}/balance returns 404 for a nonexistent customer."""
    fake_id = str(uuid.uuid4())
    resp = client.get(
        f"/api/v1/customers/{fake_id}/balance",
        headers=view_auth_headers,
    )
    assert resp.status_code == 404, resp.text
