# Business Policies

## Representative

- Representative may change selling price.
- Price change affects only current invoice.
- Customers belong only to their assigned representative.

## Inventory

- Negative stock is NOT allowed.
- Backorder is allowed.
- Inventory is calculated from immutable transactions.

## Transfer

- Transfer is two-phase (see 09_Decisions.md ADR-005): dispatch
  immediately debits the source warehouse; the destination warehouse
  is only credited once receipt is confirmed, fully (RECEIVED) or
  partially (PARTIAL_RECEIVED).
- Representative confirmation of receipt IS required at the
  destination warehouse -- corrects this section's earlier text,
  which described a single-step model that conflicted with
  06_ERD.md's TransferState enum and 02_SRS.md §9.2.

## Credit

- Customer credit limit is configurable by system administrator.

## Commission

- Commission is calculated from sales amount.

...