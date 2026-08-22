# Architectural Decisions

## ADR-001

Inventory is calculated from immutable InventoryTransaction records.

Reason:

Auditability

Status:

Accepted

-------------------------

## ADR-002

Customer is an independent Aggregate Root.

Reason:

Future CRM

Status:

Accepted

-------------------------

## ADR-003

Negative stock is forbidden.

Reason:

Financial accuracy.

Status:

Accepted

-------------------------

## ADR-004

Order (T10) state machine: full 13-state transition graph, resolving
the disagreements between 02_SRS.md §8.1/8.2/8.3 and
07_DATABASE_SPEC.md §T10.

Decisions:

1. REJECTED (used in SRS §8.3 prose) maps to CANCELLED --
   PENDING_APPROVAL -> CANCELLED. No 14th enum value added; no
   migration required.
2. Stock reservation happens AFTER manager approval: APPROVED ->
   RESERVED, per SRS §8.1/§8.3's literal state order. (This
   supersedes 07_DATABASE_SPEC.md §T10 point 7's paraphrase
   "before leaving DRAFT" -- that line should be corrected to say
   "before leaving APPROVED" the next time §T10 is edited.)
3. BACKORDERED exits only via manual resubmission
   (BACKORDERED -> PENDING_APPROVAL) or cancellation
   (BACKORDERED -> CANCELLED). No automatic background retry job.
4. PARTIALLY_FULFILLED: FULFILLING -> PARTIALLY_FULFILLED once at
   least one but not all order_line rows have qty_shipped ==
   qty_ordered (computed from existing order_line.qty_shipped /
   shipment_line data -- no new column needed); returns to the
   FULFILLING -> SHIPPED path once every line is fully shipped.
5. RETURNED is reachable only from SHIPPED and PARTIALLY_FULFILLED.
   Returns against a COMPLETED order are handled entirely through
   credit_note / customer_return without moving order.state --
   COMPLETED -> RETURNED is not a valid edge.

Reason:

Reconciles three internally-inconsistent documents
(02_SRS.md/07_DATABASE_SPEC.md/06_ERD.md) into one implementable
graph, following the most-corroborated reading at each disagreement
(see ADR-DRAFT-Order-State-Machine.md for the full comparison table
this was compiled from).

Status:

Accepted

-------------------------

## ADR-005

Stock Transfer (T4) is two-phase: DISPATCHED immediately debits the
source warehouse; the destination warehouse is only credited once
receipt is confirmed (RECEIVED, fully, or PARTIAL_RECEIVED,
partially).

Reason:

06_ERD.md's 9-state TransferState enum, 02_SRS.md §9.2's dispatch/
receipt/variance workflow, and the already-seeded separate
TRANSFER_OUT/TRANSFER_IN movement types all independently describe
this two-phase model; only 04_Business_Policies.md's "Transfer"
section described a single-step model, and is corrected by this
decision (see that file's own updated text).

Status:

Accepted

-------------------------

## ADR-006

Invoice (T17/T18) immutability triggers at state = ISSUED (i.e. any
state other than DRAFT or VOID: ISSUED, PARTIALLY_PAID, PAID,
CLOSED_CORRECTED), not at PAID/CLOSED_CORRECTED as
07_DATABASE_SPEC.md §T17 point 7 currently states.

amount_paid/balance_due remain writable post-ISSUED, but only by the
reconciliation service role (already restricted via column-level
GRANT per §T17 point 7) -- this is a column-level exception to the
state-based trigger, not a contradiction of it.

Reason:

07_DATABASE_SPEC.md §T17 (header) and §T18 (lines) gave two
different trigger points for the same table family; §T18's ISSUED
boundary is adopted because it matches §T17's own Soft Delete
Strategy line ("Supported pre-ISSUED only"), and because an
ISSUED-but-unpaid invoice already sent to a customer should not be
silently editable. §T17 point 7's trigger condition should be
corrected to match the next time that section is edited.

Status:

Accepted
