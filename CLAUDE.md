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