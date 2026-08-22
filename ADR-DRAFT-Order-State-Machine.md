# ADR-DRAFT: Order (T10) State Transition Graph

**Status: RESOLVED — see `09_Decisions.md` ADR-004 for the accepted graph.**
This file is kept only as supporting rationale/comparison table for that
decision — implement `services/order_service.py` against ADR-004, not
against this file directly.

## Why this exists

`07_DATABASE_SPEC.md` §T10 point 7 states plainly that the database only
enforces `state` being one of the 13 `OrderState` values — "not the
transition graph itself" — and that the graph is "intentionally left to
the application/service layer." No document in this repository currently
writes that graph down completely or consistently. Per `CLAUDE.md`
("Never generate code before design approval" / "No breaking changes
without ADR"), that graph has to exist and be approved *before*
`services/order_service.py` is written — otherwise the state machine gets
invented ad hoc inside application code, which is exactly what `CLAUDE.md`
is trying to prevent.

This document does two things:

1. Compiles everything the existing docs (`02_SRS.md` §8, `07_DATABASE_SPEC.md`
   §T10/T11/H5) already say about the graph into one table.
2. Flags every place those docs disagree with each other or leave a gap,
   with a proposed resolution for each — marked clearly as a proposal,
   not a decision.

Everything under "What the docs already agree on" is a direct compilation,
not a new design choice. Everything under "Open questions" needs an
explicit answer from whoever owns this project before it's implementable.

## The 13 states (not in dispute — `ck_order_state`, verbatim)

`DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `RESERVED`, `FULFILLING`,
`SHIPPED`, `INVOICED`, `PAID`, `COMPLETED`, `CANCELLED`, `BACKORDERED`,
`PARTIALLY_FULFILLED`, `RETURNED`.

## What the docs already agree on

`02_SRS.md` §8.1 gives the "happy path" spine:

```
DRAFT → PENDING_APPROVAL → APPROVED → RESERVED → FULFILLING →
SHIPPED → INVOICED → PAID → COMPLETED
```

§8.3 ("State Transitions (guarded)") gives these edges explicitly:

| From | To | Trigger (per §8.3) |
|---|---|---|
| `DRAFT` | `PENDING_APPROVAL` | submit |
| `PENDING_APPROVAL` | `APPROVED` | approval granted |
| `APPROVED` | `RESERVED` | stock reserved |
| `RESERVED` | `FULFILLING` | (unstated trigger) |
| `FULFILLING` | `SHIPPED` | (unstated trigger) |
| `SHIPPED` | `INVOICED` | (unstated trigger) |
| `INVOICED` | `PAID` | (unstated trigger) |
| `PAID` | `COMPLETED` | (unstated trigger — §8.2 says "when fully paid + fulfilled") |
| any state before `SHIPPED` | `CANCELLED` | "releases reservation" |
| any state at/after `SHIPPED` | `RETURNED` | "reverse tx" (§8.2: "Returns — if any") |

`order_status_history` (H5/T12) requires every transition to carry a
non-null `actor_user_id` and a `from_state`/`to_state` pair, both drawn
from the same `OrderState` vocabulary — so whatever graph is approved,
every edge in it needs a human (or system) actor attributable to it.

## Open questions (need an explicit decision — proposals below, not answers)

### 1. `REJECTED` is used in prose but is not a valid `state` value

§8.3 literally says `PENDING_APPROVAL → APPROVED / REJECTED`, but
`REJECTED` does not appear in `ck_order_state`'s 13-value vocabulary
(`07_DATABASE_SPEC.md` §T10 point 6). As written, a rejected order has
nowhere valid to go.

**Proposed resolution:** treat "rejected" as `PENDING_APPROVAL → CANCELLED`
(i.e. `REJECTED` in §8.3's prose was shorthand, not a distinct DB state) —
this needs zero schema change and is consistent with `CANCELLED` already
being reachable from "any state before `SHIPPED`". Alternative: add
`REJECTED` as a 14th enum value via a migration — more precise
(distinguishes "manager said no" from "customer/rep cancelled"), but is a
schema change and needs its own migration + `09_Decisions.md` entry.

### 2. Reservation timing — three documents disagree with each other

- `02_SRS.md` §8.1/§8.3 (state order): reservation happens **after**
  approval — `APPROVED → RESERVED`.
- `02_SRS.md` §8.2 (prose workflow order): lists "Validation / Reservation
  ... Reserve stock (IR-10) → status RESERVED" as a step that comes
  **before** the "Approval" step in the numbered walkthrough.
- `07_DATABASE_SPEC.md` §T10 point 7 (business constraint, quoted
  verbatim): *"LOCAL orders require a rep-warehouse reservation
  (`stock_reservation`) before leaving `DRAFT`"* — i.e. reservation
  happens at/before the **very first** transition, before
  `PENDING_APPROVAL` even.

These three cannot all be true. Three genuinely different order-taking
UX flows follow from picking one:

| Reading | Meaning | Consequence |
|---|---|---|
| (a) §8.1/§8.3 literal | Reserve only after a manager approves | An approved-but-out-of-stock order can exist transiently between `APPROVED` and the stock check |
| (b) §8.2 prose | Reserve during initial validation, before approval is even requested | Stock gets held for orders that might still be rejected by a manager |
| (c) T10 business constraint | Reserve before the order can leave `DRAFT` at all | Nothing enters the approval queue without stock already held |

**Proposed resolution:** (c) — reserve before leaving `DRAFT` — because it
is the only reading backed by an explicit, physical-spec **business
constraint** rather than workflow prose, and it also matches `BR-S6`
("a local sale cannot complete if the rep warehouse lacks sufficient
stock ... order enters backorder or is rejected") most cleanly: the
stock check has already happened by the time anyone is asked to approve
anything. Under this reading, `RESERVED` as a *state* becomes redundant
with "already reserved at DRAFT exit" — needs its own follow-up: does
`RESERVED` still exist as a separate post-`APPROVED` state (re-confirming
the hold survived the approval wait), or does the graph collapse to
`DRAFT → PENDING_APPROVAL → APPROVED → FULFILLING`, with `RESERVED`
dropped from the *effective* path (staying only as a schema enum value
that a given order might briefly report)? This sub-question is left open
deliberately rather than guessed.

### 3. `BACKORDERED` — entry and exit undefined

§8.2 says insufficient stock during validation leads to `BACKORDERED` *or*
rejection ("If insufficient → `BACKORDERED` or rejected") — but no rule
says which of the two, or who/what decides. Nor does any document say
what happens to a `BACKORDERED` order afterward: does it retry
reservation automatically (e.g. triggered by a `RECEIPT_FROM_PRODUCTION`
or `TRANSFER_IN` event), does someone manually re-submit it, or can it
only be cancelled?

**Proposed resolution (needs explicit sign-off, not just a default):**
`DRAFT → BACKORDERED` (insufficient stock at the same reservation check
point resolved in question 2) and `BACKORDERED → PENDING_APPROVAL`
(re-attempted once stock exists) or `BACKORDERED → CANCELLED`. No
auto-retry is proposed — EC1's own resolution text ("losing order
backordered") reads as a manual/queued state, not a background job, but
this project has no stated policy on that either way.

### 4. `PARTIALLY_FULFILLED` — not in §8.3's edge list at all

It's named as a "branch/terminal" in §8.1 but §8.3 never says which state
transitions into or out of it. `order_line` (T11) *does* carry
`qty_shipped <= qty_ordered`, so a line-level partial ship is clearly
modeled — the gap is purely at the order-header `state` level.

**Proposed resolution:** `FULFILLING → PARTIALLY_FULFILLED` when at least
one line ships but not all lines do, and `PARTIALLY_FULFILLED →
FULFILLING` (or directly to `SHIPPED`) once the remaining lines ship —
mirroring how `qty_shipped` already accumulates per line without a
separate "partial" sub-state at that level.

### 5. `RETURNED` — "post-shipment" is not one state

§8.2 just says "Returns — if any" reverse the transaction; §8.3 says
"Post-shipment → `RETURNED`". Taken literally that's an edge from every
one of `SHIPPED`/`INVOICED`/`PAID`/`COMPLETED`/`PARTIALLY_FULFILLED` into
`RETURNED`, which is plausible but has never been stated as a deliberate
choice (e.g. should a `COMPLETED`, fully-paid order really be allowed to
flip straight to `RETURNED`, or should returns against a completed order
be modeled as a separate `credit_note` (T18-ish) / `customer_return`
flow that leaves `order.state = COMPLETED` alone and only the newer
tables move?). `database/models` already has both `credit_note.py` and
`customer_return.py` as separate tables, which suggests returns might be
intended to live there instead of as an `order.state` transition at all.

**Proposed resolution:** keep `order.state = RETURNED` reachable only
from `SHIPPED` / `PARTIALLY_FULFILLED` (pre-completion returns/refusals),
and route post-`COMPLETED` returns entirely through
`customer_return`/`credit_note` without moving the order's own `state` —
this avoids ever needing to explain a `COMPLETED → RETURNED` edge on a
financially closed order. This is the most speculative proposal in this
document and most needs a real decision rather than a default.

## Proposed full graph (pending the answers above)

```
DRAFT ─────────────┬─────────────────────────────► CANCELLED
  │ (reserve; Q2)   └──(insufficient stock)───────► BACKORDERED ──► CANCELLED
  ▼                                                     │
PENDING_APPROVAL ───(reject; Q1)───────────────────► CANCELLED
  │ (approve)                                           ▲
  ▼                                          (stock now available)
APPROVED ──► RESERVED? (Q2) ──► FULFILLING ──┬──► SHIPPED ──► INVOICED ──► PAID ──► COMPLETED
                                              └──► PARTIALLY_FULFILLED (Q4) ──► SHIPPED

SHIPPED / PARTIALLY_FULFILLED ──► RETURNED (Q5)
```

## What happens once this is decided

1. Whoever owns the project picks an answer (or amends the proposals
   above) for each of the five open questions.
2. The accepted graph gets its own numbered entry appended to
   `09_Decisions.md` (this file can then be deleted or kept as
   supporting rationale — the ADR itself belongs there, not here).
3. Only then should `services/order_service.py` be written, following
   the exact pattern `services/rbac_service.py` / `services/customer_service.py`
   already establish (Session-taking functions, custom exceptions per
   invalid-transition case, no commit/close inside the service layer) —
   with every transition additionally writing an `order_status_history`
   row and, for `order_type` overrides specifically, an `audit_log` row
   via `services/audit_service.record()` (already available as of this
   review pass).
