# Data Cleanup Report — TEST/DEMO residue removed from the development DB

Date: 2026-09-05. Scope: the local development database (`DATABASE_URL` in
`.env`, schema `erp`).

## Why

The backend test suite and dev seed scripts historically ran against the
development database and committed rows. Over weeks this left ~48,600 TEST/DEMO
rows across ~60 tables (products, warehouses, representatives, customers, price
lists, orders, invoices, payments, inventory, commissions, bot sessions, audit
rows, test users/roles, ...), which made registering real data fail or collide
(e.g. manual deletes returned 409 because documents referenced the test rows).

## What was removed (48,521 rows)

| Table | Rows deleted | Table | Rows deleted |
|---|---:|---|---:|
| audit_log | 9 236 | product_category | 69 |
| order_status_history | 5 678 | invoice_history | 1 099 |
| app_user (test users) | 2 925 | price_history | 1 208 |
| role (test roles) | 2 693 | user_role (test grants) | 2 752 |
| role_permission (test grants) | 3 770 | customer_ledger_entry | 299 |
| inventory_transaction | 2 391 | customer_ledger | 288 |
| customer | 2 042 | order | 1 135 |
| product | 1 502 | order_line | 1 141 |
| representative | 2 130 | price_list | 1 117 |
| invoice | 641 | invoice_line | 553 |
| invoice_order | 513 | representative_contact | 629 |
| warehouse (+assignments) | 363 + 730 | customer_rep_assignment | 618 |
| discount | 680 | customer_price_list | 123 |
| stock_reservation | 987 | credit_note (+lines) | 124 + 128 |
| payment (+allocations) | 31 + 32 | stock_transfer (+lines/history) | 93 + 93 + 140 |
| bot_session / bot_binding_token | 191 / 127 | kpi_snapshot | 51 |
| approval_request / approval_history | 41 / 82 | stock_adjustment | 5 |
| report_definition / run / snapshot | 11 / 4 / 4 | commission_config / commission_transaction | 1 / 1 |
| customer_return / return_line | 1 / 1 | permission (test perms) | 8 |
| reason_code_ref / report_type_ref / unit_of_measure | 1 / 1 / 1 | product_image, attachment, generated_document, etc. (empty) | 0 |

Notes:
* 2,130 representatives removed; **2,129 of them were TEST rows and one
  (`PEP-001`, the duplicate registration from 2026-09-04) was removed at the
  owner's explicit request** — only the real `REP-001` registration is kept.
* All 9,236 audit_log rows were TEST residue (recorded actions on TEST rows).
* All warehouses were TEST/dev-seed rows (including the dev-only `MAIN`
  helper). A fresh real setup starts with zero warehouses (see
  `services/bootstrap_service.py`); the user will create real ones.

## What was deliberately kept (87 rows)

* **Users (3):** `system` (seeded bootstrap user), `admin` (real login),
  `bot_user_19874d19` (FK anchor of the real bot config — named like a test
  user but it is the actor that created/owns the live bot config).
* **Real business data (2):** representative `REP-001` «سوده جمالی» (national
  ID 3052125435, tax 2356) + its primary PHONE contact +989131917993.
* **Real bot configuration (1):** `bot_config` row for Telegram
  `FaraPista_bot` («سیستم فروش شرکت عطر مایه پسته ایرانیان», enabled, RUNNING)
  — token untouched.
* **Base seeds:** IRR currency (1); UoMs PCS/G/M/M3/PKG (5, Persian names);
  movement types (12); reason codes (6); report types (3); bot platforms
  TELEGRAM/BALE (2); ADMIN role (1); system+admin → ADMIN assignments (2);
  ADMIN → 24 bootstrap permissions grants (24); permission catalog (25 —
  includes BOT_WRITE which is intentionally not granted by default per
  ADR-008).

## Integrity

A generic FK orphan audit over every foreign key in the `erp` schema was run
inside the same transaction before commit: **no orphaned references found**.
The transaction was committed only after the audit passed.

## Environment ready?

Yes for registering real data (representatives, customers, warehouses,
products, price lists, orders, ...) — the master/transaction tables are now
empty and collision-free. Note `REP-001` already exists, so registering a new
real representative requires a different code, or the user should continue from
`REP-001`.

## Prevention (new project rule)

The following DEVELOPMENT/TESTING RULE is recorded in `CLAUDE.md` and must be
followed by every test/script going forward:

> "Tests must not leave persistent test/demo data in the development database.
> Any data created by a test must be isolated and/or cleaned up after the test."

Implementation guidance:
* Prefer transaction rollback / isolated test database. Otherwise use explicit,
  reliable fixture cleanup.
* Tests must never depend on data left behind by a previous run.
* Dev seed scripts (`scripts/dev_seed_products.py`,
  `scripts/seed_and_list_products.py`) now refuse to run unless
  `ERP_ALLOW_DEV_SEED=1` is set and their output is TEST/DEMO data that must be
  cleaned up afterwards.

## Recovery tool

`scripts/cleanup_test_data.py` performs the same kind of cleanup
transactionally (FK-enforcement suspended during delete, orphan audit before
commit, full rollback on any failure). Preview with `--dry-run`; review the
`KEEP_*` constants before re-running on a database that now holds real data.
