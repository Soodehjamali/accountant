# ADR-DRAFT: Invoice (T17/T18) — Immutability Trigger Point

**Status: RESOLVED — see `09_Decisions.md` ADR-006 for the accepted trigger point.**
Kept only as supporting rationale — implement the invoice immutability
trigger against ADR-006, not against this file directly.

## The conflict — three documents, three different answers

`BusinessInvariants.md`, INV-003, **Status: Approved** (this is the only
one of the three carrying a formal approval marker):

> Invoices are immutable.

Unqualified — no DRAFT carve-out, no state list. Taken literally, an
invoice can never be edited even before it's issued, which would make
building one impossible (you'd have to get every line right in a single
atomic insert).

`07_DATABASE_SPEC.md` §T17 point 7 (business constraint, quoted
verbatim):

> once `state IN ('PAID','CLOSED_CORRECTED')` the header and its lines
> are immutable — enforced via `BEFORE UPDATE` trigger

Two states trigger immutability: `PAID` or `CLOSED_CORRECTED`. Notably
**not** `ISSUED` or `PARTIALLY_PAID` — under this reading, an issued,
unpaid invoice could still be edited.

`07_DATABASE_SPEC.md` §T18 point 7 (`invoice_line`'s own business
constraint, in the very next table of the *same* document):

> `unit_price` copied from `order_line.unit_price` at issue time and
> never re-resolved against `price_history`; immutable once invoice is
> `ISSUED`

This is a **third** answer, and it disagrees with its own sibling
section: T18 says lines lock at `ISSUED`; T17 says the header (and, in
T17's own words, "its lines") locks only at `PAID`/`CLOSED_CORRECTED`,
two states later. `06_ERD.md` T17 also independently states the header
locks at `PAID`/`CLOSED_CORRECTED`, matching T17's spec section but not
T18's.

So: `INV-003` (approved) says "always"; the spec's own header section
says "`PAID`+"; the spec's own line-item section says "`ISSUED`+". None
of the three lines up.

## Proposed resolution

**Adopt `ISSUED`+ as the actual trigger point** — i.e. resolve the T17
vs. T18 disagreement in T18's favor — for these reasons:

1. `ISSUED` is the more defensible legal/accounting boundary: once an
   invoice document has been produced and (presumably) sent to a
   customer, its stated amounts shouldn't silently change underneath
   that document, even before payment arrives. Letting an `ISSUED`,
   unpaid invoice still be edited (T17's literal reading) would let the
   *paper the customer already has* diverge from the database.
2. `07_DATABASE_SPEC.md`'s own `Soft Delete Strategy` line for T17
   (point 12) already draws its line at `ISSUED`: *"Supported
   pre-`ISSUED` only ... post-`ISSUED` invoices are never deleted, only
   corrected via `credit_note`."* That's the same document treating
   `ISSUED` as the meaningful boundary for one kind of mutation
   (deletion) while its point-7 text picks a *different* boundary
   (`PAID`) for another kind (field edits) — internally inconsistent on
   its own terms, and the deletion rule's boundary is the one that
   matches T18.
3. This reading satisfies `INV-003` ("Invoices are immutable") as
   *"immutable once they're a real invoice"* rather than reading
   `INV-003` so literally that a `DRAFT` invoice being edited before
   anyone has seen it would violate an approved invariant — the
   `DRAFT`→`ISSUED` window has to be mutable for invoice creation to be
   possible at all, and no document actually disputes that; `INV-003`'s
   own one-line brevity looks like shorthand for "immutable once real,"
   not a considered claim that a saved-but-unissued draft can never be
   touched.

**If accepted:** `07_DATABASE_SPEC.md` §T17 point 7's trigger condition
should be corrected from `state IN ('PAID','CLOSED_CORRECTED')` to
`state IN ('ISSUED','PARTIALLY_PAID','PAID','CLOSED_CORRECTED')` (every
non-`DRAFT`, non-`VOID` state) so the `BEFORE UPDATE` trigger's actual
implementation and T18's already-stated line-level rule agree. `VOID` is
deliberately left out of the immutability set in this proposal — a
`VOID`ed invoice presumably needs its `state` field itself to still be
writable to reach `VOID` from wherever it was before, and no document
describes a `VOID`-then-uneditable two-step; this is flagged as its own
small open question, not silently resolved.

## What's still open even under the proposed resolution

- Whether `amount_paid`/`balance_due` (already carved out to the
  reconciliation service role via column-level `GRANT`, per T17 point 7)
  are meant to be exempt from the header-immutability trigger entirely
  — they clearly need to keep changing after `ISSUED` as payments come
  in, so the trigger will need a column-level exception, not just a
  state-based one. This is an implementation detail of the trigger, not
  a business-rule ambiguity, but it's real work for whoever writes the
  migration.
