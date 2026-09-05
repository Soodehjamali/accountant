# AI Development Rules

This project must follow Domain Driven Design.

Never generate code before design approval.

Inventory is always calculated from immutable InventoryTransaction.

Negative stock is forbidden.

Customer is an Aggregate Root.

Append-only ledger.

All history must be immutable.

No breaking changes without ADR.

Always update documentation before implementation.

## Testing / Development Data Hygiene (mandatory)

"Tests must not leave persistent test/demo data in the development database.
Any data created by a test must be isolated and/or cleaned up after the test."

Rules:
* Test data must use transaction rollback, fixture cleanup, or an isolated
test database. Never commit TEST/DEMO rows into the dev DB.
* Tests must never depend on data left behind by a previous test run.
* Every test that touches the database must clean up after itself (prefer
transaction rollback; otherwise explicit, reliable cleanup).
* Dev/demo seed scripts are gated: they refuse to run unless
`ERP_ALLOW_DEV_SEED=1` is set (see `scripts/dev_seed_products.py` and
`scripts/seed_and_list_products.py`).
* If the dev DB still gets polluted, run the one-time maintenance tool
`python -m scripts.cleanup_test_data` (`--dry-run` to preview) and review its
`KEEP_*` constants first. See `docs/data-cleanup-report.md`.
* Base seed data (system/admin users, RBAC role/permissions, IRR currency,
PCS (etc.) UoMs, movement types, reason codes, bot platforms, report types,
real bot config) must never be deleted by a cleanup.