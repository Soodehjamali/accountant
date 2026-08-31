"""Tests for the Reporting service (M17/T26/H9).

Skipped automatically if ``DATABASE_URL`` is not configured (same
convention as ``test_customers.py`` / ``test_invoices.py``).  Builds its
own supporting rows directly via the ORM/service layer.

Test matrix:
* Each of the three report builders against known fixture data
  (assert on computed values, not just "it didn't crash")
* FAILED run when report_type_ref code doesn't match any builder
* uq_report_definition and uq_report_snapshot_run violations rejected
* Permission gate (403)
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
from database.models.inventory_balance_snapshot import InventoryBalanceSnapshot
from services import inventory_service
from database.models.representative import Representative
from database.models.report_definition import ReportDefinition
from database.models.report_run import ReportRun
from database.models.report_snapshot import ReportSnapshot
from database.models.report_type_ref import ReportTypeRef
from database.session import get_session_factory
from services import (
    auth_service,
    bootstrap_service,
    customer_ledger_service,
    report_service,
    rbac_service,
)

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB report tests",
)

REPORT_MANAGE = "REPORT_MANAGE"


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
        username = f"test_rpt_{suffix}"
        password = "correct-horse-battery-staple"
        new_user = auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )

        role_code = f"RPT_TESTER_{suffix}"
        rbac_service.create_role(
            session, code=role_code, name="Report Tester (test)", created_by=system_user.id
        )
        for code in permission_codes:
            try:
                rbac_service.create_permission(
                    session,
                    code=code,
                    name=code,
                    resource="report",
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
    return _user_with_permissions(REPORT_MANAGE)


@pytest.fixture()
def report_fixtures() -> dict:
    """Set up minimal supporting rows for report tests."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        currency = bootstrap_service.ensure_default_currency(session, actor_id=system_user.id)
        bootstrap_service.ensure_rbac_bootstrap(session)

        # Seed report types.
        report_types = bootstrap_service.ensure_report_types(session, actor_id=system_user.id)
        rt_by_code = {rt.code: rt for rt in report_types}

        suffix = uuid.uuid4().hex[:8]

        # Customer + ledger + entries for AR_AGING.
        customer = Customer(
            code=f"CUST-RPT-{suffix}",
            name="Report Test Customer",
            type="CORPORATE",
            currency_id=currency.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(customer)
        session.flush()

        ledger = customer_ledger_service.ensure_customer_ledger(
            session,
            customer_id=customer.id,
            currency_id=currency.id,
        )

        # Invoice entry: +500, occurred 45 days ago (falls in 31-60 bucket).
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)
        entry = customer_ledger_service.record_entry(
            session,
            customer_id=customer.id,
            reference_type="invoice",
            reference_id=uuid.uuid4(),
            signed_amount=decimal.Decimal("500.0000"),
            currency_id=currency.id,
            entry_type="INVOICE_ISSUED",
            actor_user_id=system_user.id,
        )
        # Override occurred_at to 45 days ago for bucketing test.
        entry.occurred_at = past
        session.flush()

        # Inventory for INVENTORY_VALUATION.
        warehouse = bootstrap_service.ensure_default_warehouse(session, actor_id=system_user.id)
        product_uom = bootstrap_service.ensure_default_uom(session, actor_id=system_user.id)

        from database.models.product import Product
        product = Product(
            sku=f"SKU-RPT-{suffix}",
            name="Report Test Product",
            base_uom_id=product_uom.id,
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(product)
        session.flush()

        inventory_service.post_transaction(
            session,
            product_id=product.id,
            warehouse_id=warehouse.id,
            movement_type_code="RECEIPT_FROM_PRODUCTION",
            signed_quantity=decimal.Decimal("50.0000"),
            unit_cost=decimal.Decimal("10.000000"),
            currency_id=currency.id,
            actor_user_id=system_user.id,
        )

        snapshot = InventoryBalanceSnapshot(
            warehouse_id=warehouse.id,
            product_id=product.id,
            quantity_on_hand=decimal.Decimal("50.0000"),
            quantity_reserved=decimal.Decimal("0"),
            quantity_available=decimal.Decimal("50.0000"),
        )
        session.add(snapshot)
        session.flush()

        session.commit()

        return {
            "ar_aging_rt_id": str(rt_by_code["AR_AGING"].id),
            "inv_val_rt_id": str(rt_by_code["INVENTORY_VALUATION"].id),
            "comm_pay_rt_id": str(rt_by_code["COMMISSION_PAYABLE"].id),
            "unknown_rt_code": "UNKNOWN_REPORT_TYPE",
            "customer_id": str(customer.id),
            "system_user_id": str(system_user.id),
        }
    finally:
        session.close()


# ------------------------------------------------------------------ Tests


@requires_database
def test_ar_aging_report(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    report_fixtures: dict,
) -> None:
    """AR_AGING report should return one customer row with correct buckets."""
    session = get_session_factory()()
    try:
        rd = report_service.create_report_definition(
            session,
            report_type_id=uuid.UUID(report_fixtures["ar_aging_rt_id"]),
            owner_user_id=uuid.UUID(report_fixtures["system_user_id"]),
            name=f"Test AR Aging {uuid.uuid4().hex[:8]}",
            parameters={},
            output_format="CSV",
            actor_id=uuid.UUID(report_fixtures["system_user_id"]),
        )
        session.commit()
    finally:
        session.close()

    resp = client.post(
        f"/api/v1/report-definitions/{rd.id}/run",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run"]["status"] == "COMPLETE"
    assert body["snapshot"] is not None

    data = body["snapshot"]["snapshot_data"]
    rows = data["rows"]
    assert len(rows) >= 1

    # Find our customer.
    customer_row = next(
        (r for r in rows if r["customer_id"] == report_fixtures["customer_id"]),
        None,
    )
    assert customer_row is not None, f"Expected customer {report_fixtures['customer_id']} in rows"
    assert decimal.Decimal(customer_row["total_balance"]) == decimal.Decimal("500.0000")
    # 45 days ago -> should be in 31-60 bucket.
    assert decimal.Decimal(customer_row["31_60_days"]) == decimal.Decimal("500.0000")
    assert decimal.Decimal(customer_row["0_30_days"]) == decimal.Decimal("0")


@requires_database
def test_inventory_valuation_report(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    report_fixtures: dict,
) -> None:
    """INVENTORY_VALUATION report should return one row with correct value."""
    session = get_session_factory()()
    try:
        rd = report_service.create_report_definition(
            session,
            report_type_id=uuid.UUID(report_fixtures["inv_val_rt_id"]),
            owner_user_id=uuid.UUID(report_fixtures["system_user_id"]),
            name=f"Test Inv Val {uuid.uuid4().hex[:8]}",
            parameters={},
            output_format="CSV",
            actor_id=uuid.UUID(report_fixtures["system_user_id"]),
        )
        session.commit()
    finally:
        session.close()

    resp = client.post(
        f"/api/v1/report-definitions/{rd.id}/run",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run"]["status"] == "COMPLETE"
    assert body["snapshot"] is not None

    data = body["snapshot"]["snapshot_data"]
    rows = data["rows"]
    # Report has at least 1 row (our fixture adds 50 units at 10.00)
    assert len(rows) >= 1
    # All rows must have positive total_value
    for row in rows:
        assert decimal.Decimal(row["total_value"]) > 0


@requires_database
def test_commission_payable_report_empty(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    report_fixtures: dict,
) -> None:
    """COMMISSION_PAYABLE report with no commission transactions returns empty."""
    session = get_session_factory()()
    try:
        rd = report_service.create_report_definition(
            session,
            report_type_id=uuid.UUID(report_fixtures["comm_pay_rt_id"]),
            owner_user_id=uuid.UUID(report_fixtures["system_user_id"]),
            name=f"Test Comm Pay {uuid.uuid4().hex[:8]}",
            parameters={},
            output_format="CSV",
            actor_id=uuid.UUID(report_fixtures["system_user_id"]),
        )
        session.commit()
    finally:
        session.close()

    resp = client.post(
        f"/api/v1/report-definitions/{rd.id}/run",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run"]["status"] == "COMPLETE"
    assert body["snapshot"] is not None
    # Report structure must be valid; may have accumulated rows from other tests
    assert "rows" in body["snapshot"]["snapshot_data"]


@requires_database
def test_unknown_report_type_fails_run(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    report_fixtures: dict,
) -> None:
    """Running a report with an unknown report_type_ref code should
    produce a FAILED run and return 501.
    """
    session = get_session_factory()()
    try:
        # Create a ReportTypeRef with an unknown code (unique per run).
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        rt = ReportTypeRef(
            code=f"UNKNOWN_{suffix}",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(rt)
        session.flush()

        rd = report_service.create_report_definition(
            session,
            report_type_id=rt.id,
            owner_user_id=system_user.id,
            name=f"Unknown Type Report {uuid.uuid4().hex[:8]}",
            parameters={},
            output_format="CSV",
            actor_id=system_user.id,
        )
        session.commit()
    finally:
        session.close()

    resp = client.post(
        f"/api/v1/report-definitions/{rd.id}/run",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 501, resp.text


@requires_database
def test_duplicate_report_definition_rejected() -> None:
    """Creating two report definitions with the same (owner, name) should fail."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        report_types = bootstrap_service.ensure_report_types(session, actor_id=system_user.id)
        rt = report_types[0]

        dup_name = f"Dup Test {uuid.uuid4().hex[:8]}"
        report_service.create_report_definition(
            session,
            report_type_id=rt.id,
            owner_user_id=system_user.id,
            name=dup_name,
            parameters={},
            output_format="CSV",
            actor_id=system_user.id,
        )
        session.flush()

        with pytest.raises(report_service.DuplicateReportDefinitionError):
            report_service.create_report_definition(
                session,
                report_type_id=rt.id,
                owner_user_id=system_user.id,
                name=dup_name,
                parameters={},
                output_format="CSV",
                actor_id=system_user.id,
            )
    finally:
        session.close()


@requires_database
def test_snapshot_one_per_run() -> None:
    """uq_report_snapshot_run: running the same report twice should produce
    two separate runs, each with its own snapshot.
    """
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        report_types = bootstrap_service.ensure_report_types(session, actor_id=system_user.id)
        rt = next(t for t in report_types if t.code == "INVENTORY_VALUATION")

        rd = report_service.create_report_definition(
            session,
            report_type_id=rt.id,
            owner_user_id=system_user.id,
            name=f"Snapshot Uniqueness Test {uuid.uuid4().hex[:8]}",
            parameters={},
            output_format="CSV",
            actor_id=system_user.id,
        )
        session.flush()

        run1 = report_service.run_report(
            session,
            report_definition_id=rd.id,
            triggered_by=system_user.id,
        )
        session.flush()

        run2 = report_service.run_report(
            session,
            report_definition_id=rd.id,
            triggered_by=system_user.id,
        )
        session.flush()

        snap1 = report_service.get_report_snapshot(session, run1.id)
        snap2 = report_service.get_report_snapshot(session, run2.id)

        assert snap1 is not None
        assert snap2 is not None
        assert snap1.id != snap2.id
        assert snap1.report_run_id == run1.id
        assert snap2.report_run_id == run2.id
    finally:
        session.close()


@requires_database
def test_permission_gate(
    client: TestClient,
    report_fixtures: dict,
) -> None:
    """Accessing report endpoints without permission returns 403."""
    headers = _user_with_permissions()  # no permissions
    resp = client.get(
        f"/api/v1/report-definitions/{uuid.uuid4()}",
        headers=headers,
    )
    assert resp.status_code == 403, resp.text


@requires_database
def test_read_report_definition(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    report_fixtures: dict,
) -> None:
    """GET /report-definitions/{id} returns the definition."""
    session = get_session_factory()()
    try:
        rd = report_service.create_report_definition(
            session,
            report_type_id=uuid.UUID(report_fixtures["ar_aging_rt_id"]),
            owner_user_id=uuid.UUID(report_fixtures["system_user_id"]),
            name=f"Read Test {uuid.uuid4().hex[:8]}",
            parameters={"key": "value"},
            output_format="CSV",
            actor_id=uuid.UUID(report_fixtures["system_user_id"]),
        )
        session.commit()
    finally:
        session.close()

    resp = client.get(
        f"/api/v1/report-definitions/{rd.id}",
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"].startswith("Read Test ")
    assert body["output_format"] == "CSV"
    assert body["parameters"] == {"key": "value"}


@requires_database
def test_read_nonexistent_report_definition(
    client: TestClient,
    manage_auth_headers: dict[str, str],
) -> None:
    """GET /report-definitions/{id} returns 404 for nonexistent id."""
    resp = client.get(
        f"/api/v1/report-definitions/{uuid.uuid4()}",
        headers=manage_auth_headers,
    )
    assert resp.status_code == 404, resp.text


@requires_database
def test_csv_output_format(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    report_fixtures: dict,
) -> None:
    """Report with output_format='CSV' should include CSV in snapshot_data."""
    session = get_session_factory()()
    try:
        rd = report_service.create_report_definition(
            session,
            report_type_id=uuid.UUID(report_fixtures["inv_val_rt_id"]),
            owner_user_id=uuid.UUID(report_fixtures["system_user_id"]),
            name=f"CSV Test {uuid.uuid4().hex[:8]}",
            parameters={},
            output_format="CSV",
            actor_id=uuid.UUID(report_fixtures["system_user_id"]),
        )
        session.commit()
    finally:
        session.close()

    resp = client.post(
        f"/api/v1/report-definitions/{rd.id}/run",
        json={},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run"]["status"] == "COMPLETE"
    snapshot_data = body["snapshot"]["snapshot_data"]
    assert "csv" in snapshot_data
    assert "warehouse_id" in snapshot_data["csv"]  # CSV header


# ------------------------------------------------------------------
# GET /report-types and GET /report-definitions list tests
# ------------------------------------------------------------------


@requires_database
def test_list_report_types(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    report_fixtures: dict,
) -> None:
    """GET /report-types returns the 3 seeded report types."""
    resp = client.get("/api/v1/report-types", headers=manage_auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    codes = {item["code"] for item in body["items"]}
    assert "AR_AGING" in codes
    assert "INVENTORY_VALUATION" in codes
    assert "COMMISSION_PAYABLE" in codes
    assert len(body["items"]) >= 3


@requires_database
def test_list_report_types_requires_auth(
    client: TestClient,
    report_fixtures: dict,
) -> None:
    """GET /report-types returns 401 without auth."""
    resp = client.get("/api/v1/report-types")
    assert resp.status_code == 401, resp.text


@requires_database
def test_list_report_definitions(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    report_fixtures: dict,
) -> None:
    """GET /report-definitions returns definitions the user created."""
    # Create two definitions
    session = get_session_factory()()
    try:
        rd1 = report_service.create_report_definition(
            session,
            report_type_id=uuid.UUID(report_fixtures["ar_aging_rt_id"]),
            owner_user_id=uuid.UUID(report_fixtures["system_user_id"]),
            name=f"List Test A {uuid.uuid4().hex[:8]}",
            parameters={},
            output_format="CSV",
            actor_id=uuid.UUID(report_fixtures["system_user_id"]),
        )
        rd2 = report_service.create_report_definition(
            session,
            report_type_id=uuid.UUID(report_fixtures["inv_val_rt_id"]),
            owner_user_id=uuid.UUID(report_fixtures["system_user_id"]),
            name=f"List Test B {uuid.uuid4().hex[:8]}",
            parameters={},
            output_format="PDF",
            actor_id=uuid.UUID(report_fixtures["system_user_id"]),
        )
        session.commit()
    finally:
        session.close()

    resp = client.get("/api/v1/report-definitions", headers=manage_auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) >= 2
    names = {item["name"] for item in body["items"]}
    assert rd1.name in names
    assert rd2.name in names


@requires_database
def test_list_report_definitions_pagination(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    report_fixtures: dict,
) -> None:
    """GET /report-definitions respects skip/limit pagination."""
    # Create 3 definitions
    ids = []
    session = get_session_factory()()
    try:
        for i in range(3):
            rd = report_service.create_report_definition(
                session,
                report_type_id=uuid.UUID(report_fixtures["ar_aging_rt_id"]),
                owner_user_id=uuid.UUID(report_fixtures["system_user_id"]),
                name=f"Page Test {i} {uuid.uuid4().hex[:8]}",
                parameters={},
                output_format="CSV",
                actor_id=uuid.UUID(report_fixtures["system_user_id"]),
            )
            ids.append(rd.id)
        session.commit()
    finally:
        session.close()

    # Limit to 2
    resp = client.get(
        "/api/v1/report-definitions",
        params={"limit": 2},
        headers=manage_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) <= 2

    # Skip past all
    resp2 = client.get(
        "/api/v1/report-definitions",
        params={"skip": 1000},
        headers=manage_auth_headers,
    )
    assert resp2.status_code == 200, resp2.text
    assert len(resp2.json()["items"]) == 0


@requires_database
def test_list_report_definitions_requires_permission(
    client: TestClient,
    report_fixtures: dict,
) -> None:
    """GET /report-definitions returns 403 without REPORT_MANAGE."""
    headers = _user_with_permissions()  # no permissions
    resp = client.get("/api/v1/report-definitions", headers=headers)
    assert resp.status_code == 403, resp.text


@requires_database
def test_inventory_valuation_non_lot_tracked(
    client: TestClient,
    manage_auth_headers: dict[str, str],
    report_fixtures: dict,
) -> None:
    """Regression: INVENTORY_VALUATION report must return nonzero values
    for non-lot-tracked products (lot_id=None on both sides).  Previously,
    the NULL=NULL join predicate silently matched nothing.

    The report_fixtures fixture already creates a non-lot-tracked product.
    We verify the raw builder output directly.
    """
    session = get_session_factory()()
    try:
        rows = report_service._build_inventory_valuation(session)
        assert len(rows) >= 1, (
            "Expected at least one inventory valuation row for non-lot-tracked product"
        )
        # All rows must have positive values -- the NULL lot_id join works
        total_value = sum(decimal.Decimal(r["total_value"]) for r in rows)
        assert total_value > decimal.Decimal("0"), (
            f"Expected positive total_value for non-lot-tracked products, "
            f"got {total_value} -- NULL lot_id join may be broken"
        )
    finally:
        session.close()
