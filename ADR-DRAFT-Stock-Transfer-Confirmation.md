# ADR-DRAFT: Stock Transfer (T4) — Confirmation Model

**Status: RESOLVED — see `09_Decisions.md` ADR-005 for the accepted model.**
Kept only as supporting rationale — implement `services/stock_transfer_service.py`
against ADR-005, not against this file directly.

## The conflict (direct contradiction, not just a gap)

`04_Business_Policies.md`, under "Transfer", states flatly:

> Transfer immediately changes warehouse inventory.
> Representative confirmation is not required.

That describes a **single-step** transfer: post once, done, destination
stock updates immediately.

Every other document describing this table disagrees, and they agree
with *each other*:

- `06_ERD.md` PART A defines `TransferState` as 9 values: `DRAFT`,
  `PENDING`, `APPROVED`, `DISPATCHED`, `IN_TRANSIT`, `RECEIVED`,
  `PARTIAL_RECEIVED`, `CLOSED`, `CANCELLED`.
- `06_ERD.md` T4/T5/T6 describe a transfer header with
  `dispatched_at`/`received_at` timestamps, a `transfer_line` table
  tracking `qty_requested`/`qty_dispatched`/`qty_received` as three
  separate quantities, and an explicit business constraint: *"cannot
  receive more than dispatched per line."*
- `02_SRS.md` §9.2 spells out the same **two-phase, double-entry** flow:
  dispatch at the factory posts `TRANSFER_OUT` (stock leaves source
  immediately), receipt at the rep warehouse posts `TRANSFER_IN`
  separately (stock only lands at the destination once someone
  confirms), with a documented discrepancy-handling step
  ("`TRANSFER_OUT` posted; `TRANSFER_IN` not posted; `ADJUSTMENT_NEGATIVE`
  at source OR `ADJUSTMENT` at dest for variance") that is only
  meaningful if receipt is a distinct, confirmable event.
- `movement_type_ref`'s seeded vocabulary (`services/bootstrap_service.py`)
  already has **separate** `TRANSFER_OUT` and `TRANSFER_IN` movement
  types with opposite signs — a single-step model wouldn't need two.

A single "immediately changes inventory, no confirmation" step and a
9-state header with a distinct dispatch/receipt/partial-receipt/variance
workflow cannot both be the design. Three independent documents
(ERD, SRS, and the already-implemented movement-type seed data) agree
with each other; only `04_Business_Policies.md` disagrees.

## Proposed resolution

**Follow the ERD/SRS two-phase model; treat `04_Business_Policies.md`'s
"Transfer" section as stale/superseded**, for three reasons:

1. It's outnumbered — ERD, SRS §9.2, and the seeded `movement_type_ref`
   data all independently describe the same two-phase flow; only the
   one-paragraph policy note describes a single step.
2. The two-phase model is the only one of the two that can produce
   `PARTIAL_RECEIVED` and the documented variance-handling path at all
   — a single "immediately changes inventory" step has no room for a
   receiving party to receive less than what was dispatched.
3. `database/models` doesn't have a `stock_transfer.py` model yet (T4/T5/T6
   are still unbuilt), so adopting the two-phase reading costs nothing
   in rework — the single-step reading is the one that would require
   contradicting the ERD/SRS once those models are written.

**If this is accepted:** `04_Business_Policies.md`'s "Transfer" section
should be rewritten (not just ignored) once the ADR lands, so a future
reader doesn't hit the same contradiction again. Suggested replacement
text, pending approval: *"Transfer is two-phase: dispatch immediately
debits the source warehouse; the destination warehouse is only credited
once receipt is confirmed (fully or partially). See `09_Decisions.md`
ADR-XXX."*

## What's still open even under the proposed resolution

- Who can confirm receipt — anyone at the destination warehouse, or
  specifically the assigned representative? `02_SRS.md` §10.2 lists
  "Receive transfers from factory (confirm receipt)" as a rep's own
  day-to-day action, which suggests representative-only, but this isn't
  stated as a rule anywhere, only implied by which persona the docs
  happen to describe doing it.
- Timeout/SLA for an `IN_TRANSIT` transfer that's never confirmed —
  no document mentions one. Out of scope for the confirmation-model
  question itself, but worth flagging for whoever builds the transfer
  service.
