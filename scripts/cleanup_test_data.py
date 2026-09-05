"""One-time, controlled cleanup of TEST/DEMO residue in the development database.

Background
----------
The backend test suite (and dev/demo seed scripts) historically ran directly
against the development database and committed rows, leaving tens of thousands
of TEST rows behind (products, warehouses, representatives, customers, price
lists, orders, invoices, payments, inventory, commissions, bot sessions, audit
rows, test users/roles, ...).  This script removes exactly that residue.

What is KEPT (never touched)
----------------------------
* ``app_user``: ``system`` and ``admin``, plus every app_user referenced by the
  real ``bot_config`` row(s) as created_by/updated_by (a real Telegram/Bale bot
  configuration must not lose its FK anchor).
* ``representative``: only the codes listed in ``KEEP_REPRESENTATIVE_CODES``
  (currently ``REP-001`` = the real representative registered on 2026-09-05).
  The user confirmed the older duplicate ``PEP-001`` (2026-09-04) is to be
  removed.
* Base/master seeds: IRR currency, UoMs PCS/G/M/M3/PKG, the 12 movement types,
  the 6 bootstrap reason codes, the 3 report types, both bot platforms, the
  ADMIN role, its grants (role_permission) to the 25 bootstrap permissions,
  the 25 bootstrap permission rows, and the system/admin role assignments.
* ``bot_config``: all rows (the real Telegram config must survive).

Everything else in the schema is treated as TEST/DEMO and deleted.

Safety
------
* Runs inside ONE transaction.  Any failure (including a failed post-delete FK
  integrity audit) rolls the whole transaction back.
* FK enforcement is suspended only while the rows are physically deleted
  (needed to break benign reference cycles between the to-be-deleted rows,
  e.g. test app_user <-> test representative), then re-enabled.
* BEFORE commit the script runs a generic orphan audit over every FK
  constraint in the ``erp`` schema: if any surviving row references a missing
  parent, the transaction is rolled back.

Usage
-----
    venv/Scripts/python.exe -m scripts.cleanup_test_data          # run cleanup
    venv/Scripts/python.exe -m scripts.cleanup_test_data --dry-run  # report only

IMPORTANT: review/keep the KEEP_* constants at the top before re-running on a
database that may now hold real data.  This script is a maintenance tool for
cleaning up after accidental TEST pollution, not a routine operation.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig")

from sqlalchemy import text  # noqa: E402

from database.session import get_session  # noqa: E402

# ---------------------------------------------------------------------------
# KEEP configuration (natural keys, stable across UUIDs)
# ---------------------------------------------------------------------------
KEEP_APP_USERNAMES: list[str] = ["system", "admin"]
#: Real representative codes to preserve (REP-001).  PEP-001 was confirmed by
#: the owner as an older duplicate attempt and is intentionally NOT listed.
KEEP_REPRESENTATIVE_CODES: list[str] = ["REP-001"]
KEEP_UOM_CODES: list[str] = ["PCS", "G", "M", "M3", "PKG"]
KEEP_CURRENCY_CODES: list[str] = ["IRR"]
KEEP_BOT_PLATFORM_CODES: list[str] = ["TELEGRAM", "BALE"]
KEEP_REASON_CODE_CODES: list[str] = [
    "PRICING_ERROR", "DAMAGED_GOODS", "WRONG_ITEM_SHIPPED", "QUALITY_ISSUE",
    "COUNT_VARIANCE", "SCRAP_GOODS",
]
KEEP_REPORT_TYPE_CODES: list[str] = [
    "AR_AGING", "INVENTORY_VALUATION", "COMMISSION_PAYABLE",
]
KEEP_PERMISSION_CODES: list[str] = [
    "RBAC_MANAGE", "BOT_WRITE", "APPROVE", "AUDIT_LOG_VIEW", "BOT_MANAGE",
    "BOT_QUERY", "COMMISSION_MANAGE", "CREDIT_NOTE_MANAGE",
    "CUSTOMER_LEDGER_MANAGE", "CUSTOMER_LEDGER_VIEW", "CUSTOMER_MANAGE",
    "INVENTORY_MANAGE", "INVOICE_MANAGE", "KPI_SNAPSHOT_MANAGE",
    "KPI_SNAPSHOT_VIEW", "ORDER_APPROVE", "ORDER_MANAGE", "PAYMENT_MANAGE",
    "PRICE_LIST_MANAGE", "PRODUCT_MANAGE", "REPORT_MANAGE",
    "REPRESENTATIVE_MANAGE", "RETURN_MANAGE", "TRANSFER_MANAGE",
    "WAREHOUSE_MANAGE",
]
KEEP_ROLE_CODE: str = "ADMIN"


# ---------------------------------------------------------------------------
# Derived keep predicates (referenced repeatedly by DELETE statements)
# ---------------------------------------------------------------------------
def _in_list(col: str, values: list[str]) -> str:
    if not values:
        return "FALSE"
    quoted = ", ".join(f"'{v}'" for v in values)
    return f'{col} IN ({quoted})'


USERNAMES = _in_list("username", KEEP_APP_USERNAMES)
REP_CODES = _in_list("code", KEEP_REPRESENTATIVE_CODES)
UOM_CODES = _in_list("code", KEEP_UOM_CODES)
CURRENCY_CODES = _in_list("code", KEEP_CURRENCY_CODES)
PLATFORM_CODES = _in_list("code", KEEP_BOT_PLATFORM_CODES)
REASON_CODES = _in_list("code", KEEP_REASON_CODE_CODES)
REPORT_TYPE_CODES = _in_list("code", KEEP_REPORT_TYPE_CODES)
PERMISSION_CODES = _in_list("code", KEEP_PERMISSION_CODES)

# app_user kept = system/admin + owners of real bot_config rows
APP_USER_KEEP = (
    f"username IN ('system','admin') OR id IN ("
    f"  SELECT created_by FROM erp.bot_config "
    f"  UNION SELECT updated_by FROM erp.bot_config)"
)
# representatives kept by code (via sub-select so the same rule is reused)
REP_KEEP_SUB = f"SELECT id FROM erp.representative WHERE {REP_CODES}"


# ---------------------------------------------------------------------------
# What to delete.  (kind='all' -> whole table; kind='keep' -> all but keep)
# ---------------------------------------------------------------------------
# 1) junctions/history/log/cache tables: safe to empty first.
# (role_permission/user_role are NOT here -- they have keep-rules below.)
JUNCTIONS_AND_LOGS: list[str] = [
    "order_status_history", "transfer_history",
    "invoice_history", "approval_history", "approval_request",
    "shipment_status_history", "audit_log", "notification",
    "notification_history", "order_price_freeze", "payment_allocation",
    "invoice_order", "bot_message_log", "kpi_snapshot",
    "customer_ledger_entry", "customer_ledger", "inventory_balance_snapshot",
    "stock_reservation", "report_run", "report_snapshot", "report_definition",
    "generated_document", "attachment", "product_image",
]

# 2) transactional documents and their lines
TRANSACTIONS: list[str] = [
    "return_line", "customer_return", "credit_note_line", "credit_note",
    "payment", "invoice_line", "invoice", "order_line", "order",
    "shipment_line", "shipment", "transfer_line", "stock_transfer",
    "stock_adjustment", "physical_count_line", "physical_count",
    "inventory_transaction",
]

# 3) masters/catalogs (referenced only by the tables above once cleaned)
MASTERS: list[str] = [
    "price_history", "customer_price_list", "customer_rep_assignment",
    "customer_contact", "discount", "price_list", "warehouse_assignment",
    "warehouse_location", "warehouse", "customer", "commission_config",
    "product_lot", "product_serial", "product", "product_category",
    "uom_conversion", "notification_type_ref", "city_ref", "carrier",
    "costing_method_config", "credit_limit_config", "system_config",
    "bot_binding_token", "bot_session", "commission_transaction",
]

# 4) special tables: delete-but-keep rule per table
SPECIAL_KEEP: list[tuple[str, str, str]] = [
    # (table, column_name the predicate applies to, predicate text)
    ("app_user", "id", APP_USER_KEEP),
    ("representative", "id", f"id IN ({REP_KEEP_SUB})"),
    ("representative_contact", "representative_id",
     f"representative_id IN ({REP_KEEP_SUB})"),
    ("unit_of_measure", "code", UOM_CODES),
    ("currency", "code", CURRENCY_CODES),
    ("bot_platform_ref", "code", PLATFORM_CODES),
    ("reason_code_ref", "code", REASON_CODES),
    ("report_type_ref", "code", REPORT_TYPE_CODES),
    ("permission", "code", PERMISSION_CODES),
    ("movement_type_ref", "code",
     _in_list("code", [
         "RECEIPT_FROM_PRODUCTION", "TRANSFER_IN", "TRANSFER_OUT", "SALE_OUT",
         "SALE_RETURN_IN", "ADJUSTMENT_POSITIVE", "ADJUSTMENT_NEGATIVE",
         "DAMAGED_OUT", "FACTORY_DIRECT_SHIPMENT", "INITIAL_OPENING_BALANCE",
         "REVERSAL", "CONSIGNMENT_SELLTHROUGH_OWNERSHIP"])),
    # role: keep only ADMIN
    ("role", "code", _in_list("code", [KEEP_ROLE_CODE])),
]

# 5) never deleted (real user data / live configuration)
NEVER_DELETE: list[str] = ["bot_config"]


def _erp_columns(conn: object) -> set[str]:
    rows = conn.execute(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='erp' AND table_type='BASE TABLE'"
    )).fetchall()
    return {r[0] for r in rows}


def _count_rows(conn: object, table: str) -> int:
    return conn.execute(
        text(f'SELECT count(*) FROM erp."{table}"')
    ).scalar_one()


def _orphan_audit(conn: object) -> list[str]:
    """Return a list of FK violations found among *surviving* rows.

    Builds one anti-join per FK constraint in the erp schema and counts child
    rows whose referenced parent is missing.
    """
    problems: list[str] = []
    fks = conn.execute(text(
        """
        SELECT c.conname,
               ct.relname AS child_tbl,
               pt.relname AS parent_tbl,
               c.conkey, c.confkey
        FROM pg_constraint c
        JOIN pg_class ct ON ct.oid = c.conrelid
        JOIN pg_class pt ON pt.oid = c.confrelid
        JOIN pg_namespace n ON n.oid = c.connamespace
        WHERE c.contype = 'f' AND n.nspname = 'erp'
        ORDER BY ct.relname, c.conname
        """
    )).fetchall()
    for conname, child_tbl, parent_tbl, conkey, confkey in fks:
        child_cols = []
        parent_cols = []
        for ca, pa in zip(conkey, confkey):
            child_cols.append(conn.execute(text(
                "SELECT a.attname FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = :t AND n.nspname='erp' AND a.attnum = :n"
            ), {"t": child_tbl, "n": int(ca)}).scalar_one())
            parent_cols.append(conn.execute(text(
                "SELECT a.attname FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = :t AND n.nspname='erp' AND a.attnum = :n"
            ), {"t": parent_tbl, "n": int(pa)}).scalar_one())
        joins = " AND ".join(
            f'ch."{cc}" = p."{pc}"' for cc, pc in zip(child_cols, parent_cols)
        )
        notnull = " AND ".join(f'ch."{cc}" IS NOT NULL' for cc in child_cols)
        # parent PK col used to detect a missing row
        first_parent = parent_cols[0]
        broken = conn.execute(text(
            f'SELECT count(*) FROM erp."{child_tbl}" ch '
            f'LEFT JOIN erp."{parent_tbl}" p ON {joins} '
            f'WHERE {notnull} AND p."{first_parent}" IS NULL'
        )).scalar_one()
        if broken:
            problems.append(
                f"FK {conname}: {broken} orphaned row(s) in "
                f"{child_tbl} -> {parent_tbl}"
            )
    return problems


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    with get_session() as session:
        tables = _erp_columns(session)

        # -- pre-cleanup inventory ---------------------------------------
        pre: dict[str, int] = {}
        for t in sorted(tables):
            pre[t] = _count_rows(session, t)
        total_pre = sum(pre.values())
        print(f"Total rows before cleanup: {total_pre} across {len(tables)} tables")

        # -- build delete plans -------------------------------------------
        delete_all: list[str] = []
        delete_keep: list[tuple[str, str, str]] = []
        for t in sorted(tables):
            if t in NEVER_DELETE:
                continue
            if t in JUNCTIONS_AND_LOGS + TRANSACTIONS + MASTERS:
                delete_all.append(t)
        # role_permission keep rule (ADMIN -> bootstrap permissions only)
        delete_keep.append((
            "role_permission", "1",
            f"(role_id IN (SELECT id FROM erp.role WHERE code = '{KEEP_ROLE_CODE}') "
            f"AND permission_id IN (SELECT id FROM erp.permission WHERE {PERMISSION_CODES}))",
        ))
        # user_role keep rule (system/admin assigned ADMIN)
        delete_keep.append((
            "user_role", "1",
            f"(user_id IN (SELECT id FROM erp.app_user WHERE {APP_USER_KEEP}) "
            f"AND role_id IN (SELECT id FROM erp.role WHERE code = '{KEEP_ROLE_CODE}'))",
        ))
        for t, col, pred in SPECIAL_KEEP:
            if t in tables and t not in delete_all and t not in NEVER_DELETE:
                delete_keep.append((t, col, pred))
        # tables handled by name lists that are NOT present are ignored
        delete_all = [t for t in delete_all if t in tables]

        plan: list[tuple[str, str]] = []  # (kind, table)
        for t in delete_all:
            plan.append(("all", t))
        for t, col, pred in delete_keep:
            plan.append(("keep", t))
        # deterministic order for a readable log
        plan = sorted(plan, key=lambda item: item[1])

        # -- dry run / execute --------------------------------------------
        if dry_run:
            print("\n-- DELETE PLAN (dry run, nothing executed) --")
            for kind, t in plan:
                n = pre[t]
                if kind == "all":
                    print(f"  DELETE ALL   {t:<40} ({n} rows)")
                else:
                    print(f"  KEEP RULE    {t:<40} ({n} rows)")
            print(f"\n  never deleted: {', '.join(NEVER_DELETE)}")
            return

        print("\nExecuting cleanup in one transaction ...")
        deleted_total = 0

        # suspend FK enforcement for the tables we empty
        targets = sorted({t for _, t in plan})
        for t in targets:
            session.execute(text(f'ALTER TABLE erp."{t}" DISABLE TRIGGER ALL'))
        try:
            for kind, t in plan:
                if kind == "all":
                    res = session.execute(text(f'DELETE FROM erp."{t}"'))
                else:
                    # keep rows: find the actual keep predicate
                    pred = next(
                        p for (tt, col, p) in delete_keep if tt == t
                    )
                    res = session.execute(
                        text(f'DELETE FROM erp."{t}" WHERE NOT ({pred})')
                    )
                n = res.rowcount
                deleted_total += n
                if n:
                    print(f"  deleted {n:>7} rows from {t}")
        finally:
            for t in targets:
                session.execute(text(f'ALTER TABLE erp."{t}" ENABLE TRIGGER ALL'))

        # -- integrity audit before commit ---------------------------------
        print("\nRunning FK orphan audit ...")
        problems = _orphan_audit(session)
        if problems:
            for p in problems:
                print(f"  VIOLATION: {p}")
            raise SystemExit(
                "FK integrity audit failed -- transaction rolled back; "
                "nothing was committed."
            )
        print("  FK audit clean: no orphaned references.")

    # committed on clean context exit
    print(f"\nCleanup committed. Rows deleted: {deleted_total}")

    # -- post-cleanup inventory -------------------------------------------
    with get_session() as session:
        tables = _erp_columns(session)
        print("\nRemaining rows after cleanup:")
        grand = 0
        for t in sorted(tables):
            n = _count_rows(session, t)
            grand += n
            if n:
                print(f"  {t:<40} {n:>6}")
        print(f"TOTAL remaining: {grand}")


if __name__ == "__main__":
    main()
