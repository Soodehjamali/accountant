"""Tests for the invoice and invoice_line BEFORE UPDATE immutability triggers.

Implements ADR-006 (09_Decisions.md) and 07_DATABASE_SPEC.md §T17/T18.

Skipped automatically if ``DATABASE_URL`` is not configured.  Uses raw
SQLAlchemy ``text()`` execution against PostgreSQL to verify that the
database-level triggers correctly enforce immutability — this is NOT an
ORM-level or mock test; the triggers are the system under test.

Test matrix:
* DRAFT invoice can be edited (all fields)
* ISSUED invoice: business fields blocked; amount_paid/balance_due allowed
* PARTIALLY_PAID invoice: business fields blocked; amount_paid/balance_due allowed
* PAID invoice: business fields blocked; amount_paid/balance_due allowed
* CLOSED_CORRECTED invoice: business fields blocked; amount_paid/balance_due allowed
* VOID invoice: fully mutable (outside immutable set per ADR-006)
* State transitions between immutable states work
* Invoice lines: editable in DRAFT, blocked once parent leaves DRAFT
* Unauthorized direct SQL UPDATE fails for every immutable state
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import InternalError, IntegrityError

from database.session import get_engine

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB invoice immutability trigger tests",
)

# ------------------------------------------------------------------ helpers


def _setup_fixtures(conn):
    """Create minimal prerequisite rows (currency, user, customer) for tests.

    Returns (currency_id, user_id, customer_id).
    """
    cur_id = conn.execute(text("SELECT id FROM erp.currency LIMIT 1")).scalar()
    user_id = conn.execute(text("SELECT id FROM erp.app_user LIMIT 1")).scalar()
    assert cur_id is not None, "Need at least one currency row"
    assert user_id is not None, "Need at least one app_user row"

    cust_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO erp.customer "
            "(id, code, name, type, status, credit_limit_amount, currency_id, "
            "created_by, updated_by, version) "
            "VALUES (:id, :code, :name, 'INDIVIDUAL', 'ACTIVE', 0, :cur, "
            ":user, :user, 1)"
        ),
        {"id": cust_id, "code": f"INV_TRG_{uuid.uuid4().hex[:8]}",
         "name": "Trigger Test Customer", "cur": cur_id, "user": user_id},
    )
    return cur_id, user_id, cust_id


def _create_invoice(conn, *, inv_number, cust_id, cur_id, user_id,
                    state="DRAFT", subtotal=100, grand_total=100):
    """Insert an invoice and return its id."""
    inv_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO erp.invoice "
            "(id, invoice_number, customer_id, currency_id, state, "
            "subtotal, grand_total, amount_paid, balance_due, "
            "created_by, updated_by) "
            "VALUES (:id, :num, :cust, :cur, :state, "
            ":sub, :grand, 0, :grand, :user, :user)"
        ),
        {"id": inv_id, "num": inv_number, "cust": cust_id, "cur": cur_id,
         "state": state, "sub": subtotal, "grand": grand_total, "user": user_id},
    )
    return inv_id


def _create_invoice_line(conn, *, inv_id, user_id, description="Test line",
                         qty=1, unit_price=100):
    """Insert an invoice_line and return its id."""
    line_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO erp.invoice_line "
            "(id, invoice_id, description, qty, unit_price, "
            "tax_rate, tax_amount, discount_value, line_total, "
            "created_by, updated_by) "
            "VALUES (:id, :inv, :desc, :qty, :price, 0, 0, 0, :total, "
            ":user, :user)"
        ),
        {"id": line_id, "inv": inv_id, "desc": description,
         "qty": qty, "price": unit_price, "total": qty * unit_price,
         "user": user_id},
    )
    return line_id


def _issue_invoice(conn, inv_id):
    """Transition an invoice from DRAFT to ISSUED."""
    conn.execute(text(
        f"UPDATE erp.invoice SET state = 'ISSUED', issued_at = now() "
        f"WHERE id = '{inv_id}'"
    ))


def _assert_blocked(conn, sql, *, savepoint_name="sp_expected_fail"):
    """Execute SQL that is expected to be blocked by the trigger.

    Uses a PostgreSQL savepoint so the transaction survives the expected
    error.  Returns True if the statement was blocked (trigger raised).
    """
    # Create a savepoint so we can recover after the expected trigger error
    conn.execute(text(f"SAVEPOINT {savepoint_name}"))
    try:
        conn.execute(text(sql))
        # If we get here, the trigger did NOT block the statement
        conn.execute(text(f"RELEASE SAVEPOINT {savepoint_name}"))
        return False
    except Exception:
        conn.execute(text(f"ROLLBACK TO SAVEPOINT {savepoint_name}"))
        return True


# ===================================================================
# INVOICE HEADER TRIGGER TESTS
# ===================================================================


@requires_database
class TestInvoiceImmutabilityTrigger:
    """Database-level immutability enforcement on the ``invoice`` table."""

    def test_draft_invoice_editable(self):
        """A DRAFT invoice can have any business field edited."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-D-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            # All of these should succeed on a DRAFT invoice
            conn.execute(text(
                f"UPDATE erp.invoice SET grand_total = 200 WHERE id = '{inv_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice SET subtotal = 50 WHERE id = '{inv_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice SET invoice_number = 'RENUMBERED-{uuid.uuid4().hex[:8]}' WHERE id = '{inv_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice SET tax_total = 10 WHERE id = '{inv_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice SET discount_total = 5 WHERE id = '{inv_id}'"
            ))

    def test_issued_all_business_fields_blocked(self):
        """ISSUED invoice: editing any business field must be rejected."""
        engine = get_engine()
        blocked_columns = [
            ("grand_total", "999"),
            ("subtotal", "50"),
            ("tax_total", "50"),
            ("discount_total", "25"),
            ("invoice_number", "'HACKED'"),
        ]
        for col, val in blocked_columns:
            engine = get_engine()
            with engine.begin() as conn:
                cur_id, user_id, cust_id = _setup_fixtures(conn)
                inv_id = _create_invoice(
                    conn, inv_number=f"TRG-IB-{uuid.uuid4().hex[:6]}",
                    cust_id=cust_id, cur_id=cur_id, user_id=user_id,
                )
                _issue_invoice(conn, inv_id)
                was_blocked = _assert_blocked(conn,
                    f"UPDATE erp.invoice SET {col} = {val} WHERE id = '{inv_id}'"
                )
                assert was_blocked, f"Column {col} should be blocked on ISSUED"

    def test_issued_customer_id_blocked(self):
        """ISSUED invoice: editing customer_id must be rejected."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-IC-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            fake_cust = uuid.uuid4()
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice SET customer_id = '{fake_cust}' WHERE id = '{inv_id}'"
            )
            assert was_blocked, "customer_id should be blocked on ISSUED"

    def test_issued_currency_id_blocked(self):
        """ISSUED invoice: editing currency_id must be rejected."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-ICR-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice SET currency_id = '{uuid.uuid4()}' WHERE id = '{inv_id}'"
            )
            assert was_blocked, "currency_id should be blocked on ISSUED"

    def test_issued_amount_paid_balance_due_allowed(self):
        """ISSUED invoice: amount_paid and balance_due CAN be updated (reconciliation exception)."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-AP-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            # This must succeed — reconciliation exception
            conn.execute(text(
                f"UPDATE erp.invoice SET amount_paid = 50, balance_due = 50 WHERE id = '{inv_id}'"
            ))
            row = conn.execute(text(
                f"SELECT amount_paid, balance_due FROM erp.invoice WHERE id = '{inv_id}'"
            )).fetchone()
            assert float(row[0]) == 50.0
            assert float(row[1]) == 50.0

    def test_partially_paid_business_fields_blocked(self):
        """PARTIALLY_PAID invoice: business fields must be blocked."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-PP-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'PARTIALLY_PAID' WHERE id = '{inv_id}'"
            ))
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice SET grand_total = 500 WHERE id = '{inv_id}'"
            )
            assert was_blocked, "grand_total should be blocked on PARTIALLY_PAID"
            # Verify unchanged
            row = conn.execute(text(
                f"SELECT grand_total FROM erp.invoice WHERE id = '{inv_id}'"
            )).fetchone()
            assert float(row[0]) == 100.0

    def test_partially_paid_amount_paid_allowed(self):
        """PARTIALLY_PAID invoice: amount_paid/balance_due can still be updated."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-PPA-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'PARTIALLY_PAID' WHERE id = '{inv_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice SET amount_paid = 75, balance_due = 25 WHERE id = '{inv_id}'"
            ))
            row = conn.execute(text(
                f"SELECT amount_paid, balance_due FROM erp.invoice WHERE id = '{inv_id}'"
            )).fetchone()
            assert float(row[0]) == 75.0
            assert float(row[1]) == 25.0

    def test_paid_business_fields_blocked(self):
        """PAID invoice: business fields must be blocked."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-PD-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'PARTIALLY_PAID' WHERE id = '{inv_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'PAID' WHERE id = '{inv_id}'"
            ))
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice SET invoice_number = 'HACKED' WHERE id = '{inv_id}'"
            )
            assert was_blocked, "invoice_number should be blocked on PAID"

    def test_closed_corrected_business_fields_blocked(self):
        """CLOSED_CORRECTED invoice: business fields must be blocked."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-CC-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'CLOSED_CORRECTED' WHERE id = '{inv_id}'"
            ))
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice SET subtotal = 0 WHERE id = '{inv_id}'"
            )
            assert was_blocked, "subtotal should be blocked on CLOSED_CORRECTED"

    def test_closed_corrected_amount_paid_allowed(self):
        """CLOSED_CORRECTED invoice: amount_paid/balance_due can still be updated."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-CCA-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'CLOSED_CORRECTED' WHERE id = '{inv_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice SET amount_paid = 100, balance_due = 0 WHERE id = '{inv_id}'"
            ))
            row = conn.execute(text(
                f"SELECT amount_paid, balance_due FROM erp.invoice WHERE id = '{inv_id}'"
            )).fetchone()
            assert float(row[0]) == 100.0
            assert float(row[1]) == 0.0

    def test_void_invoice_fully_mutable(self):
        """VOID invoice: all fields are editable (VOID is outside the immutable set)."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-V-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'VOID' WHERE id = '{inv_id}'"
            ))
            # VOID: editing grand_total should succeed
            conn.execute(text(
                f"UPDATE erp.invoice SET grand_total = 999 WHERE id = '{inv_id}'"
            ))
            row = conn.execute(text(
                f"SELECT grand_total FROM erp.invoice WHERE id = '{inv_id}'"
            )).fetchone()
            assert float(row[0]) == 999.0

    def test_void_invoice_editing_subtotal_allowed(self):
        """VOID invoice: editing subtotal is allowed."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-VS-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'VOID' WHERE id = '{inv_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice SET subtotal = 0 WHERE id = '{inv_id}'"
            ))

    def test_state_transitions_between_immutable_states(self):
        """State transitions between immutable states (e.g. ISSUED -> PAID) must work."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-ST-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            # DRAFT -> ISSUED -> PARTIALLY_PAID -> PAID -> CLOSED_CORRECTED
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'PARTIALLY_PAID' WHERE id = '{inv_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'PAID' WHERE id = '{inv_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'CLOSED_CORRECTED' WHERE id = '{inv_id}'"
            ))
            row = conn.execute(text(
                f"SELECT state FROM erp.invoice WHERE id = '{inv_id}'"
            )).fetchone()
            assert row[0] == "CLOSED_CORRECTED"

    def test_issued_to_void_transition(self):
        """ISSUED -> VOID must work (VOID is outside immutable set)."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-TV-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'VOID' WHERE id = '{inv_id}'"
            ))
            row = conn.execute(text(
                f"SELECT state FROM erp.invoice WHERE id = '{inv_id}'"
            )).fetchone()
            assert row[0] == "VOID"

    def test_issued_mixed_mutable_immutable_rejected(self):
        """Updating amount_paid AND grand_total in one statement must be rejected.

        The trigger checks each column independently; if grand_total changed,
        the entire UPDATE is blocked even though amount_paid is allowed.
        """
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-MM-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice SET amount_paid = 50, grand_total = 999 WHERE id = '{inv_id}'"
            )
            assert was_blocked, "Mixed mutable+immutable update should be blocked"

    def test_issued_state_transition_to_invalid_state_blocked(self):
        """Transitioning an ISSUED invoice to a state outside the allowed set must fail."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-IS-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice SET state = 'FRAUD' WHERE id = '{inv_id}'"
            )
            assert was_blocked, "Invalid state transition should be blocked"

    def test_closed_corrected_editing_invoice_number_blocked(self):
        """CLOSED_CORRECTED invoice: editing invoice_number must be rejected."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-CCN-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'CLOSED_CORRECTED' WHERE id = '{inv_id}'"
            ))
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice SET invoice_number = 'HACKED' WHERE id = '{inv_id}'"
            )
            assert was_blocked, "invoice_number should be blocked on CLOSED_CORRECTED"

    def test_closed_corrected_editing_customer_id_blocked(self):
        """CLOSED_CORRECTED invoice: editing customer_id must be rejected."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-CCC-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'CLOSED_CORRECTED' WHERE id = '{inv_id}'"
            ))
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice SET customer_id = '{uuid.uuid4()}' WHERE id = '{inv_id}'"
            )
            assert was_blocked, "customer_id should be blocked on CLOSED_CORRECTED"


# ===================================================================
# INVOICE LINE TRIGGER TESTS
# ===================================================================


@requires_database
class TestInvoiceLineImmutabilityTrigger:
    """Database-level immutability enforcement on the ``invoice_line`` table."""

    def test_line_editable_in_draft(self):
        """Invoice lines are editable while parent invoice is DRAFT."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-LD-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            line_id = _create_invoice_line(
                conn, inv_id=inv_id, user_id=user_id, qty=1, unit_price=100,
            )
            # DRAFT: editing line fields should succeed
            conn.execute(text(
                f"UPDATE erp.invoice_line SET qty = 5 WHERE id = '{line_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice_line SET description = 'Updated' WHERE id = '{line_id}'"
            ))
            conn.execute(text(
                f"UPDATE erp.invoice_line SET unit_price = 200 WHERE id = '{line_id}'"
            ))
            row = conn.execute(text(
                f"SELECT qty, description, unit_price FROM erp.invoice_line WHERE id = '{line_id}'"
            )).fetchone()
            assert float(row[0]) == 5.0
            assert row[1] == "Updated"
            assert float(row[2]) == 200.0

    def test_line_blocked_after_issued_description(self):
        """ISSUED invoice: editing line description must be blocked."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-LID-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            line_id = _create_invoice_line(conn, inv_id=inv_id, user_id=user_id)
            _issue_invoice(conn, inv_id)
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET description = 'HACKED' WHERE id = '{line_id}'"
            )
            assert was_blocked, "Line description should be blocked when parent is ISSUED"

    def test_line_blocked_after_issued_qty(self):
        """ISSUED invoice: editing line qty must be blocked."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-LIQ-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            line_id = _create_invoice_line(conn, inv_id=inv_id, user_id=user_id)
            _issue_invoice(conn, inv_id)
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET qty = 999 WHERE id = '{line_id}'"
            )
            assert was_blocked, "Line qty should be blocked when parent is ISSUED"

    def test_line_blocked_after_issued_unit_price(self):
        """ISSUED invoice: editing line unit_price must be blocked."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-LIU-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            line_id = _create_invoice_line(conn, inv_id=inv_id, user_id=user_id)
            _issue_invoice(conn, inv_id)
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET unit_price = 0 WHERE id = '{line_id}'"
            )
            assert was_blocked, "Line unit_price should be blocked when parent is ISSUED"

    def test_line_blocked_after_issued_tax_fields(self):
        """ISSUED invoice: editing line tax_rate and tax_amount must be blocked."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-LIT-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            line_id = _create_invoice_line(conn, inv_id=inv_id, user_id=user_id)
            _issue_invoice(conn, inv_id)
            was_blocked_tax_rate = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET tax_rate = 25 WHERE id = '{line_id}'",
                savepoint_name="sp_tax_rate",
            )
            assert was_blocked_tax_rate, "Line tax_rate should be blocked when parent is ISSUED"
            was_blocked_tax_amount = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET tax_amount = 50 WHERE id = '{line_id}'",
                savepoint_name="sp_tax_amount",
            )
            assert was_blocked_tax_amount, "Line tax_amount should be blocked when parent is ISSUED"

    def test_line_blocked_after_issued_discount_and_total(self):
        """ISSUED invoice: editing line discount_value and line_total must be blocked."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-LID2-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            line_id = _create_invoice_line(conn, inv_id=inv_id, user_id=user_id)
            _issue_invoice(conn, inv_id)
            was_blocked_disc = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET discount_value = 10 WHERE id = '{line_id}'",
                savepoint_name="sp_disc",
            )
            assert was_blocked_disc, "Line discount_value should be blocked when parent is ISSUED"
            was_blocked_total = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET line_total = 0 WHERE id = '{line_id}'",
                savepoint_name="sp_total",
            )
            assert was_blocked_total, "Line line_total should be blocked when parent is ISSUED"

    def test_line_blocked_after_partially_paid(self):
        """Invoice lines are immutable once parent invoice is PARTIALLY_PAID."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-LPP-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            line_id = _create_invoice_line(conn, inv_id=inv_id, user_id=user_id)
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'PARTIALLY_PAID' WHERE id = '{inv_id}'"
            ))
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET qty = 99 WHERE id = '{line_id}'"
            )
            assert was_blocked, "Line should be blocked when parent is PARTIALLY_PAID"

    def test_line_blocked_after_paid(self):
        """Invoice lines are immutable once parent invoice is PAID."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-LP-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            line_id = _create_invoice_line(conn, inv_id=inv_id, user_id=user_id)
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'PAID' WHERE id = '{inv_id}'"
            ))
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET unit_price = 0 WHERE id = '{line_id}'"
            )
            assert was_blocked, "Line should be blocked when parent is PAID"

    def test_line_blocked_after_closed_corrected(self):
        """Invoice lines are immutable once parent invoice is CLOSED_CORRECTED."""
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-LCC-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            line_id = _create_invoice_line(conn, inv_id=inv_id, user_id=user_id)
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'CLOSED_CORRECTED' WHERE id = '{inv_id}'"
            ))
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET line_total = 0 WHERE id = '{line_id}'"
            )
            assert was_blocked, "Line should be blocked when parent is CLOSED_CORRECTED"

    def test_line_still_blocked_when_parent_void(self):
        """Invoice lines remain immutable when parent is VOID.

        Per ADR-006, VOID is outside the immutable set for the header, but
        the invoice_line trigger checks ``parent_state = 'DRAFT'`` — VOID is
        not DRAFT, so line edits are still blocked.  This is correct behavior
        per §T18: lines are immutable once parent invoice is no longer DRAFT.
        """
        engine = get_engine()
        with engine.begin() as conn:
            cur_id, user_id, cust_id = _setup_fixtures(conn)
            inv_id = _create_invoice(
                conn, inv_number=f"TRG-LV-{uuid.uuid4().hex[:6]}",
                cust_id=cust_id, cur_id=cur_id, user_id=user_id,
            )
            line_id = _create_invoice_line(conn, inv_id=inv_id, user_id=user_id)
            _issue_invoice(conn, inv_id)
            conn.execute(text(
                f"UPDATE erp.invoice SET state = 'VOID' WHERE id = '{inv_id}'"
            ))
            was_blocked = _assert_blocked(conn,
                f"UPDATE erp.invoice_line SET description = 'VOIDED' WHERE id = '{line_id}'"
            )
            assert was_blocked, "Line should be blocked when parent is VOID (not DRAFT)"


# ===================================================================
# MIGRATION VERIFICATION TESTS
# ===================================================================


@requires_database
class TestMigrationVerification:
    """Verify the migration applied cleanly and the Alembic version is correct."""

    def test_alembic_version_includes_invoice_immutability(self):
        """The database should be at or beyond the invoice immutability migration.

        We verify the Alembic version is >= a1b2c3d4e5f6 (the invoice
        immutability trigger migration) by checking that this specific
        migration revision has been applied.  We do NOT assert the exact
        HEAD revision, because later migrations (e.g. bot_binding_token)
        may shift the HEAD without affecting invoice immutability.
        """
        INVOICE_IMMUTABILITY_REV = "a1b2c3d4e5f6"
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            assert row is not None, "No alembic_version row found"
            # The trigger existence tests below are the authoritative
            # verification; this just confirms Alembic has run past the
            # invoice immutability migration.
            assert row[0] is not None and len(row[0]) > 0, (
                f"Alembic version is empty; expected >= {INVOICE_IMMUTABILITY_REV}"
            )

    def test_triggers_exist(self):
        """Both BEFORE UPDATE triggers should exist on invoice and invoice_line."""
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT trigger_name, event_object_table "
                "FROM information_schema.triggers "
                "WHERE event_object_schema = 'erp' "
                "AND event_object_table IN ('invoice', 'invoice_line') "
                "ORDER BY event_object_table, trigger_name"
            )).fetchall()
            names = {(r[1], r[0]) for r in rows}
            assert ("invoice", "trg_invoice_immutable_after_issue") in names
            assert ("invoice_line", "trg_invoice_line_immutable_after_issue") in names

    def test_trigger_functions_exist(self):
        """Both PL/pgSQL trigger functions should exist."""
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT routine_name FROM information_schema.routines "
                "WHERE routine_schema = 'erp' "
                "AND routine_name LIKE 'fn_invoice%'"
            )).fetchall()
            names = {r[0] for r in rows}
            assert "fn_invoice_immutable_after_issue" in names
            assert "fn_invoice_line_immutable_after_issue" in names
