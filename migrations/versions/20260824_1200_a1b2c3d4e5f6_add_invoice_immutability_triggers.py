"""Add invoice immutability BEFORE UPDATE triggers

Implements ADR-006 (09_Decisions.md) and 07_DATABASE_SPEC.md §T17/T18:
- Invoice header: immutable once state IN ('ISSUED','PARTIALLY_PAID','PAID','CLOSED_CORRECTED')
  except amount_paid/balance_due (reconciliation exception) and audit columns.
- Invoice lines: immutable once parent invoice state <> 'DRAFT'.

Revision ID: a1b2c3d4e5f6
Revises: 2b3846cb93c5
Create Date: 2026-08-24 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '2b3846cb93c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. Invoice header immutability trigger function
    # -----------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION erp.fn_invoice_immutable_after_issue()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            -- DRAFT and VOID invoices are fully mutable.
            IF OLD.state IN ('DRAFT', 'VOID') OR OLD.state IS NULL THEN
                RETURN NEW;
            END IF;

            -- Post-ISSUED: state must remain one of the immutable set
            -- (state transitions are allowed but must target a valid state).
            IF NEW.state NOT IN ('ISSUED', 'PARTIALLY_PAID', 'PAID',
                                 'CLOSED_CORRECTED', 'VOID') THEN
                RAISE EXCEPTION 'Invoice state transition to ''%'' is not allowed for an invoice in state ''%''',
                    NEW.state, OLD.state;
            END IF;

            -- Detect which columns are actually changing (exclude NULL→value
            -- for columns that were NULL on OLD and are now set, since that
            -- is also a mutation of the column).
            IF NEW.invoice_number IS DISTINCT FROM OLD.invoice_number THEN
                RAISE EXCEPTION 'Cannot modify column "invoice_number" on an issued invoice (state=%)', OLD.state;
            END IF;

            IF NEW.customer_id IS DISTINCT FROM OLD.customer_id THEN
                RAISE EXCEPTION 'Cannot modify column "customer_id" on an issued invoice (state=%)', OLD.state;
            END IF;

            IF NEW.currency_id IS DISTINCT FROM OLD.currency_id THEN
                RAISE EXCEPTION 'Cannot modify column "currency_id" on an issued invoice (state=%)', OLD.state;
            END IF;

            IF NEW.subtotal IS DISTINCT FROM OLD.subtotal THEN
                RAISE EXCEPTION 'Cannot modify column "subtotal" on an issued invoice (state=%)', OLD.state;
            END IF;

            IF NEW.tax_total IS DISTINCT FROM OLD.tax_total THEN
                RAISE EXCEPTION 'Cannot modify column "tax_total" on an issued invoice (state=%)', OLD.state;
            END IF;

            IF NEW.discount_total IS DISTINCT FROM OLD.discount_total THEN
                RAISE EXCEPTION 'Cannot modify column "discount_total" on an issued invoice (state=%)', OLD.state;
            END IF;

            IF NEW.grand_total IS DISTINCT FROM OLD.grand_total THEN
                RAISE EXCEPTION 'Cannot modify column "grand_total" on an issued invoice (state=%)', OLD.state;
            END IF;

            -- amount_paid and balance_due are intentionally NOT checked here.
            -- Per ADR-006 and §T17 point 7, these columns remain writable
            -- post-ISSUED as a column-level exception for the reconciliation
            -- service role (column-level GRANT in deployment).

            RETURN NEW;
        END;
        $fn$;
    """)

    # -----------------------------------------------------------------
    # 2. Invoice header trigger
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TRIGGER trg_invoice_immutable_after_issue
            BEFORE UPDATE ON erp.invoice
            FOR EACH ROW
            EXECUTE FUNCTION erp.fn_invoice_immutable_after_issue();
    """)

    # -----------------------------------------------------------------
    # 3. Invoice line immutability trigger function
    # -----------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION erp.fn_invoice_line_immutable_after_issue()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $fn$
        DECLARE
            parent_state VARCHAR(24);
        BEGIN
            SELECT state INTO parent_state
              FROM erp.invoice
             WHERE id = NEW.invoice_id;

            IF parent_state = 'DRAFT' THEN
                RETURN NEW;
            END IF;

            -- Non-DRAFT parent: all line columns are immutable.
            IF NEW.description IS DISTINCT FROM OLD.description THEN
                RAISE EXCEPTION 'Cannot modify invoice_line when parent invoice is in state ''%''', parent_state;
            END IF;

            IF NEW.qty IS DISTINCT FROM OLD.qty THEN
                RAISE EXCEPTION 'Cannot modify invoice_line when parent invoice is in state ''%''', parent_state;
            END IF;

            IF NEW.unit_price IS DISTINCT FROM OLD.unit_price THEN
                RAISE EXCEPTION 'Cannot modify invoice_line when parent invoice is in state ''%''', parent_state;
            END IF;

            IF NEW.tax_rate IS DISTINCT FROM OLD.tax_rate THEN
                RAISE EXCEPTION 'Cannot modify invoice_line when parent invoice is in state ''%''', parent_state;
            END IF;

            IF NEW.tax_amount IS DISTINCT FROM OLD.tax_amount THEN
                RAISE EXCEPTION 'Cannot modify invoice_line when parent invoice is in state ''%''', parent_state;
            END IF;

            IF NEW.discount_value IS DISTINCT FROM OLD.discount_value THEN
                RAISE EXCEPTION 'Cannot modify invoice_line when parent invoice is in state ''%''', parent_state;
            END IF;

            IF NEW.line_total IS DISTINCT FROM OLD.line_total THEN
                RAISE EXCEPTION 'Cannot modify invoice_line when parent invoice is in state ''%''', parent_state;
            END IF;

            RETURN NEW;
        END;
        $fn$;
    """)

    # -----------------------------------------------------------------
    # 4. Invoice line trigger
    # -----------------------------------------------------------------
    op.execute("""
        CREATE TRIGGER trg_invoice_line_immutable_after_issue
            BEFORE UPDATE ON erp.invoice_line
            FOR EACH ROW
            EXECUTE FUNCTION erp.fn_invoice_line_immutable_after_issue();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_invoice_line_immutable_after_issue ON erp.invoice_line;")
    op.execute("DROP FUNCTION IF EXISTS erp.fn_invoice_line_immutable_after_issue();")
    op.execute("DROP TRIGGER IF EXISTS trg_invoice_immutable_after_issue ON erp.invoice;")
    op.execute("DROP FUNCTION IF EXISTS erp.fn_invoice_immutable_after_issue();")
