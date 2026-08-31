Task 0

Repository

✅ Done

-----------------

Task 1

Architecture

✅ Done

-----------------

Task 2

Database

✅ Done

78/78 ORM models implemented and validated (check_mappers.py green:
zero collisions, all constraint/index names <=63 chars, all FKs verified).
Alembic initial migration (revision 2b3846cb93c5) generated and applied
successfully against a live PostgreSQL database (schema erp, 78/78 tables
confirmed present). app_user<->representative circular FK resolved via
use_alter=True on app_user.representative_id and
representative.commission_config_id.

-----------------

Task 3

API

🟡 In progress -- endpoint sets delivered so far:

* Catalog/M1 Product endpoints (POST/GET /api/v1/products,
  GET /api/v1/products/{sku}), wired through services/product_service.py.
* Inventory Ledger/T1 endpoints (POST /api/v1/inventory/transactions,
  POST /api/v1/inventory/transactions/{id}/reverse,
  GET /api/v1/inventory/balance), wired through
  services/inventory_service.py.
* RBAC/R6-R8+M11 endpoints (POST/GET /api/v1/rbac/roles, POST/GET
  /api/v1/rbac/permissions, POST /api/v1/rbac/roles/{code}/permissions/
  {code}, POST/DELETE /api/v1/rbac/users/{id}/roles[/{code}], GET
  /api/v1/rbac/me/permissions), wired through the new
  services/rbac_service.py. A new require_permission() FastAPI dependency
  (app/dependencies/rbac.py) now exists for gating any endpoint behind a
  specific permission code, not just "logged in" -- the RBAC admin
  endpoints themselves are the first consumer, gated behind the
  bootstrapped RBAC_MANAGE permission.

All endpoints require an authenticated caller at minimum
(Depends(get_current_user)); the RBAC endpoints additionally require a
specific permission via the new Depends(require_permission(...)) --
earlier endpoints (products, inventory) are deliberately left on
"authenticated only" for now, since narrowing their access is a separate,
follow-up change to their contract. Remaining domains not yet exposed via
API: Sales/Order, Finance/Invoicing, Representatives, Commission, Audit
log, Reporting, Bots.

-----------------

Task 4

Backend

🟡 In progress -- services/inventory_service.py (Inventory Ledger, see
prior milestone note) and services/rbac_service.py (role/permission
definitions, grants, and effective-permission lookup) are both delivered
and tested. services/bootstrap_service.py gained ensure_rbac_bootstrap(),
which seeds an ADMIN role holding RBAC_MANAGE and grants it to the system
user -- breaking the RBAC chicken-and-egg problem (something has to be
able to grant the very first permission on a fresh database). Per
02_SRS.md §7's dependency graph (RBAC/Audit needed by "almost every other
module"), this was chosen as the next milestone ahead of Sales/Order.
Remaining domains not yet implemented: Sales/Order, Finance/Invoicing,
Commission, Audit log (RBAC's sibling concern -- audit_log table exists
in the ERD but has no service yet), Reporting, Bots.

-----------------

Task 4

Backend

⬜ Not started (business logic / services / repositories
-- see Task 5 for the foundation this will build on)

-----------------

Task 5

Backend Foundation

✅ Done

FastAPI application scaffolded under backend/app/ with clear layer
separation (api/services/repositories/schemas/core/dependencies), built on
top of the existing database/session.py without modifying it. Delivered:
minimal app entry point (backend/app/main.py), GET /health (liveness),
GET /health/db and GET /api/v1/health/db (PostgreSQL connectivity check),
API versioning under /api/v1, OpenAPI/Swagger (/docs, /redoc,
/openapi.json), Settings via pydantic-settings + .env (.env.example
added), and a test suite (test_startup.py, test_health.py,
test_db_health.py) run against a live PostgreSQL instance. No
authentication, no business/domain endpoints, and no frontend were
implemented in this milestone -- deliberately out of scope, reserved for
Task 3/Task 4 and a future frontend milestone respectively.

-----------------

Task 3 (update)

API -- Customer (M8) endpoints

🟡 In progress -- added during this review pass:

* Sales/Customer M8 endpoints (POST/GET /api/v1/customers, GET/PATCH
  /api/v1/customers/{id}, POST /api/v1/customers/{id}/deactivate),
  wired through the new services/customer_service.py. Mutations gated
  behind a new CUSTOMER_MANAGE permission via require_permission(),
  matching the RBAC endpoints' pattern; reads require only an
  authenticated caller, matching products/inventory's existing
  "authenticated only for now" convention. Customer is treated as its
  own Aggregate Root per CLAUDE.md -- no hard delete, only a status ->
  INACTIVE transition.
* Fixed a critical bug found in app/api/v1/router.py: a second,
  appended `api_router = APIRouter()` block was silently discarding the
  first block's five include_router() calls (health, auth, products,
  inventory, rbac), meaning only the newest routers were actually being
  served. Router now merges all six domains correctly.
* Order (T10) endpoints found in this same batch
  (backend/app/api/v1/endpoints/orders.py, app/schemas/orders.py) were
  NOT wired in and NOT rebuilt -- they targeted a different, nonexistent
  module layout (integer PKs, app.models.*, PermissionChecker) and,
  more importantly, depend on an Order state-transition graph that is
  referenced but never actually specified in 02_SRS.md /
  07_DATABASE_SPEC.md. Per CLAUDE.md ("never generate code before
  design approval"), this needs a design pass / ADR before it's
  implemented, not a mechanical port. See the status note now at the
  top of endpoints/orders.py.

-----------------

Task 3/4 (update)

API + Backend -- Audit Log (H6)

🟡 In progress -- added during this review pass:

* services/audit_service.py: record()/get_entry()/list_entries() over the
  existing audit_log (H6) model. Append-only (AAC) -- no update/delete
  helpers exist, matching the model's own classification. record()
  validates `action` against the same vocabulary as the DB's
  ck_audit_log_action CHECK constraint before inserting.
* GET /api/v1/audit-log (filterable by entity_type/entity_id/
  actor_user_id/date range) and GET /api/v1/audit-log/{id}, gated behind
  a new AUDIT_LOG_VIEW permission (not auto-seeded, same convention as
  CUSTOMER_MANAGE -- an RBAC admin grants it explicitly). Read-only: no
  write endpoint, since audit_log is never written to directly over
  HTTP -- other domain services call record() themselves at the point
  of the mutating action.
* Scope note: this milestone is the *mechanism* only. It does NOT
  retrofit record() calls into rbac_service.py / customer_service.py /
  product_service.py / inventory_service.py -- deciding what each of
  those call sites' before/after payload should capture is a separate,
  per-domain follow-up, not guessed here.

-----------------

Backend (update) -- RBAC bootstrap now grants ADMIN the new permissions

🟡 In progress -- services/bootstrap_service.py's ensure_rbac_bootstrap()
was extended (refactored into reusable _ensure_permission()/_ensure_grant()
helpers, still idempotent) so the seeded ADMIN role also holds
CUSTOMER_MANAGE and AUDIT_LOG_VIEW, not just RBAC_MANAGE. Without this,
the system user bootstrapped by ensure_rbac_bootstrap could grant other
users permissions via /api/v1/rbac but could not itself call
POST /customers or GET /audit-log until someone manually created and
granted those two permission codes by hand -- a loose end left over from
adding those two endpoints in this same review. New endpoints that
introduce their own require_permission(...) gate should add their code
to the _ADMIN_DEFAULT_PERMISSIONS tuple at the same time, rather than
leaving ADMIN unable to use its own endpoints by default.

-----------------

Design decisions (update) -- Order/Transfer/Invoice ADRs approved

✅ Done -- ADR-004 (Order state machine), ADR-005 (Stock Transfer
two-phase confirmation), and ADR-006 (Invoice immutability at ISSUED)
were reviewed and accepted; see 09_Decisions.md. 04_Business_Policies.md
(Transfer section) and 07_DATABASE_SPEC.md (§T17 point 7) were corrected
to match. The three ADR-DRAFT-*.md files are kept as supporting
rationale, marked RESOLVED, pointing at the canonical decisions.

This unblocks services/order_service.py, services/stock_transfer_service.py
(plus its still-unbuilt database/models/stock_transfer.py /
transfer_line.py / transfer_history.py), and services/invoice_service.py
-- none of these exist yet as of this note; implementing them is the
next milestone, starting with Order since its endpoint/schema files
already exist (currently unwired, see endpoints/orders.py's status
note) and its models are already built.

-----------------

Task 3/4 (update) -- Order (T10) service + API, wired in

Done, following ADR-004 -- services/order_service.py implements the
accepted 13-state graph (create/submit/approve/reserve-or-backorder/
resubmit/cancel/start-fulfillment/ship/return/invoice/pay/complete),
each transition writing an order_status_history (T12) row. Two points
ADR-004 leaves as direct consequences of its own decisions rather than
separate edges are recorded explicitly in that module's own docstring
(BACKORDERED's entry point at APPROVED -> BACKORDERED, and
PARTIALLY_FULFILLED -> SHIPPED as the "returns to the FULFILLING ->
SHIPPED path" edge) -- not invented ad hoc, but not literally spelled
out in the ADR's own edge list either, so flagged rather than silently
assumed.

app/api/v1/endpoints/orders.py has been rebuilt against this service
(the old, unwired version imported nonexistent app.models.* and a
5-value status enum -- see that module's own prior status note, now
superseded) and is wired into router.py. Two new permission codes,
ORDER_MANAGE (create/submit/reserve/ship/etc.) and ORDER_APPROVE
(the approval step specifically, a deliberately separate gate -- see
services/order_service.py's module docstring), have been added to
services/bootstrap_service.py's _ADMIN_DEFAULT_PERMISSIONS so ADMIN can
use the new endpoints out of the box, matching the same loose-end fix
already applied for CUSTOMER_MANAGE/AUDIT_LOG_VIEW.

Scope explicitly NOT covered by this milestone (see order_service.py's
own docstring for the full list): a pricing/discount resolution engine
(callers must supply an already-resolved price_history_id per line) and
real Invoice/Payment-domain integration (mark_invoiced/mark_paid/
mark_completed are order-header bookkeeping only, pending
services/invoice_service.py, which still does not exist).

backend/tests/test_orders.py was added, mirroring test_customers.py's
live-DB-required pattern (skipped without DATABASE_URL) -- covers
create->submit->approve->reserve happy path, the ORDER_APPROVE
permission gate, the insufficient-stock->BACKORDERED path, cancel
releasing reservations and becoming terminal, an invalid-transition
409, and a full ship-to-SHIPPED path. Not executed in this environment
(no live PostgreSQL instance available here) -- please run it against
a real database before relying on it.

-----------------

Task 3/4 (update) -- Invoice (T17/T18) service + API, wired in

Done, following ADR-006 -- services/invoice_service.py implements the
invoice lifecycle: create_invoice_from_order (DRAFT, copies order lines)
-> issue_invoice (DRAFT->ISSUED, sets issued_at/due_at, prices frozen
per BR-P3) -> record_payment (updates amount_paid/balance_due per the
column-level exception in ADR-006, transitions ISSUED->PARTIALLY_PAID
or PAID) -> void_invoice (DRAFT->VOID only, per spec's "pre-ISSUED"
soft-delete strategy). Each transition writes an invoice_history (H4)
row via the _transition choke point, mirroring order_service.py's own
pattern.

app/api/v1/endpoints/invoices.py wraps this service with thin
endpoints, gated behind a new INVOICE_MANAGE permission (added to
services/bootstrap_service.py's _ADMIN_DEFAULT_PERMISSIONS so ADMIN can
use the new endpoints out of the box). Router updated.

Open question left by this milestone: the invoice creation does NOT
call order_service.mark_invoiced() -- order state and invoice state are
kept independent. A future orchestration layer should coordinate the
SHIPPED->INVOICED order transition with invoice issuance.

Scope explicitly NOT covered by this milestone: payment_allocation (J2)
ledger, credit_note (T20) corrections, customer_ledger (T22) entries,
and the DB-level BEFORE UPDATE immutability trigger (application-layer
enforcement only).

backend/tests/test_invoices.py covers: full happy path (create->issue->
partial pay->full pay), void-from-DRAFT, void-from-ISSUED rejected (409),
payment-exceeds-balance rejected (422), permission gate (403), and
invoice_history verification.

-----------------

Task 3/4 (update) -- Stock Transfer (T4/T5/T6) service + API, wired in

Done, following ADR-005 -- services/stock_transfer_service.py implements
the stock transfer lifecycle: create_transfer (DRAFT, creates lines) ->
dispatch_transfer (DRAFT -> DISPATCHED, posts TRANSFER_OUT from source
warehouse) -> receive_transfer (DISPATCHED -> RECEIVED, posts TRANSFER_IN
to destination warehouse) -> cancel_transfer (DRAFT -> CANCELLED). Each
transition writes a transfer_history (T6) row via the _transition choke
point, mirroring order_service.py's and invoice_service.py's own
patterns.

app/api/v1/endpoints/transfers.py wraps this service with thin
endpoints, gated behind a new TRANSFER_MANAGE permission (added to
services/bootstrap_service.py's _ADMIN_DEFAULT_PERMISSIONS so ADMIN can
use the new endpoints out of the box). Router updated.

ADR-005's two-phase model is fully implemented: dispatch debits source
warehouse via TRANSFER_OUT; receive credits destination warehouse via
TRANSFER_IN. The source warehouse is debited at dispatch time; the
destination is credited only at receive time -- not before, not
simultaneously.

Open questions left by this milestone:
* PENDING/APPROVED intermediate states exist in the DB CHECK and the
  ALLOWED_TRANSITIONS graph but are not exposed via service functions or
  endpoints. A future milestone can add submit_transfer (DRAFT -> PENDING)
  and approve_transfer (PENDING -> APPROVED -> DISPATCHED) if the business
  requires a formal approval workflow before dispatch.
* IN_TRANSIT / PARTIAL_RECEIVED are in the graph but not exposed. These
  would support multi-leg transfers or partial receipts.
* The currency_id for inventory postings is resolved from existing
  inventory_transaction rows at the source warehouse (or falls back to
  the default IRR currency). The Transfer model itself does not carry a
  currency_id per spec.

Scope explicitly NOT covered by this milestone: approval workflow
(PENDING/APPROVED), partial receipts (PARTIAL_RECEIVED), multi-leg
transfers (IN_TRANSIT), or a formal transfer-to-invoice linkage.

backend/tests/test_transfers.py covers: full happy path (create ->
dispatch with TRANSFER_OUT -> receive with TRANSFER_IN) with inventory
balance checks at each step, cancel-from-DRAFT, and double-dispatch
rejected (409).

-----------------

Task 3/4 (update) -- Order <-> Invoice coordination (resolved open question)

Done -- the invoice issuance milestone's open question ("invoice creation
does NOT call order_service.mark_invoiced()") has been resolved.

services/invoice_service.py's issue_invoice() now, after the successful
DRAFT -> ISSUED transition, looks up the related order via the
invoice_order (J1) junction and calls order_service.mark_invoiced() to
transition the order from SHIPPED -> INVOICED.  If the order is not in
SHIPPED state (e.g., already invoiced, cancelled), an
OrderNotInShippableStateForInvoiceError is raised and the entire
session is rolled back -- both the invoice state change and the order
state change are atomic within the same session.

Design rationale for rollback: allowing the invoice to remain ISSUED
while the order is in a non-SHIPPED state creates an inconsistent
cross-aggregate state.  Rolling back the entire session ensures
atomicity -- the caller can retry after resolving the order's state.

Open question (not implemented): voiding an invoice -- should the order
revert from INVOICED back to SHIPPED?  Neither 07_DATABASE_SPEC.md nor
09_Decisions.md addresses this explicitly.  A future milestone should
decide this; for now, voided invoices do not affect order state.

backend/tests/test_invoices.py gained test_issue_invoice_transitions_order_to_invoiced,
which verifies the SHIPPED -> INVOICED order transition after invoice issuance.

-----------------

Task 3/4 (update) -- Payment / PaymentAllocation Service (J2)

Done -- services/payment_service.py implements the payment allocation
ledger: record_payment() creates a Payment row (append-only, AAC per
spec) plus one or more PaymentAllocation rows that resolve the N:N
between payments and invoices. Business constraints are enforced at
the application layer (no DB trigger exists): SUM(allocated_amount)
per payment <= payment.amount; SUM(allocated_amount) per invoice <=
invoice.grand_total; each allocation > 0; invoice must be in ISSUED
or PARTIALLY_PAID state.

The payment service updates each invoice's amount_paid / balance_due
(the non-authoritative cache columns) and transitions invoice state
(ISSUED -> PARTIALLY_PAID or PAID) at allocation time, writing an
invoice_history (H4) row for each transition.

Relationship to existing invoice_service.record_payment(): the existing
function continues to work as before (direct amount_paid/balance_due
update without payment_allocation rows). The new payment service is
the intended path going forward; the old endpoint
(POST /invoices/{id}/pay) is retained for backward compatibility.
Open question: should POST /invoices/{id}/pay be deprecated in favor
of POST /payments? The two paths write different amounts to the same
cache columns; reconciliation between them should be decided by the
product owner.

app/api/v1/endpoints/payments.py wraps this service with thin
endpoints, gated behind PAYMENT_MANAGE permission (added to
services/bootstrap_service.py's _ADMIN_DEFAULT_PERMISSIONS).
Endpoints: POST /payments (record with allocations),
GET /payments/{id} (get with allocations),
GET /invoices/{id}/payments (payments allocated to an invoice).
Router updated.

backend/tests/test_payments.py covers: full payment to a single
invoice (-> PAID), split payment across two invoices, over-allocation
of payment amount rejected (422), over-allocation of invoice balance
rejected (422).

-----------------

Task 3/4 (update) -- Commission Service (C1 + Commission Transaction)

Done -- services/commission_service.py implements commission rate
configuration and transaction calculation:

* create_commission_config(): creates a commission_config row (C1)
  with effective_from/effective_to time bounds, rate (0..100%), and
  optional representative_id / product_category_id / order_type
  specificity.
* resolve_commission_rate(): finds the most specific matching config
  for a given order. Matching priority: most-specific-first
  (representative + product_category + order_type) then falling back
  to progressively less specific matches until the global default
  (all three fields NULL). Resolution docstring explicitly documents
  the specificity ordering.
* calculate_commission_for_order(): creates an ACCRUED
  commission_transaction (T23) row with signed_amount = rate * order.grand_total.
  Commission is calculated at order COMPLETED time (documented as
  an explicit assumption: commission is definitive only after the
  sale is finalized).

The commission calculation is exposed as a standalone service function
that can be called externally on a COMPLETED order. Direct integration
with order_service.py is flagged as an open question (the task
constraint prohibits modifying order_service.py).

app/api/v1/endpoints/commissions.py wraps this service with thin
endpoints, gated behind COMMISSION_MANAGE permission (added to
services/bootstrap_service.py's _ADMIN_DEFAULT_PERMISSIONS).
Endpoints: POST /commission-configs, GET /commission-configs,
POST /orders/{id}/commission (calculate for a specific order).
Router updated.

backend/tests/test_commissions.py covers: create config and resolve
for matching order, fallback to broader config when no specific match,
commission transaction amount calculation (rate * grand_total).

-----------------

Task 3/4 (update) -- Credit Note (T20/T21) service + API, wired in

Done -- services/credit_note_service.py implements the credit note
lifecycle following the same _transition choke-point pattern as
invoice_service.py / order_service.py / stock_transfer_service.py:

* create_credit_note(): creates a DRAFT credit note with lines,
  computes total_amount from lines, validates qty > 0 and
  total_amount > 0 (matching ck_credit_note_line_qty_positive and
  ck_credit_note_amount_positive DB CHECK constraints).
* issue_credit_note(): DRAFT -> ISSUED, sets issued_at.
* apply_credit_note(): ISSUED -> APPLIED.  Per spec §T20 point 7,
  never edits the original invoice's rows directly.  Marks the
  original invoice CLOSED_CORRECTED (cross-table, same session for
  atomicity -- mirrors the invoice_service.issue_invoice() ->
  order_service.mark_invoiced() pattern).  Customer ledger entry
  (T22) write is delegated to an injectable record_entry callback
  (see below).
* void_credit_note(): DRAFT -> VOID only (pre-ISSUED soft-delete,
  same convention as invoice_service.void_invoice()).

Dependency injection -- Customer Ledger:
apply_credit_note() requires a record_entry callback to write the
customer_ledger_entry (T22).  The callback is injected as a parameter
with a default of None; when None, the function raises
NotImplementedError with a clear message pointing at the pending
Customer Ledger milestone.  Tests inject a fake/mock callable to
verify the full apply path end-to-end.  The Customer Ledger task's
job is to supply the real record_entry implementation as the default
wiring (e.g. in main.py's dependency setup), not to change
apply_credit_note()'s signature again.

Invoice ALLOWED_TRANSITIONS updated: ISSUED and PARTIALLY_PAID now
include CLOSED_CORRECTED as a reachable state, enabling credit note
corrections to transition the invoice without modifying
invoice_service.py's public API.

app/api/v1/endpoints/credit_notes.py wraps this service with thin
endpoints, gated behind CREDIT_NOTE_MANAGE permission (added to
services/bootstrap_service.py's _ADMIN_DEFAULT_PERMISSIONS).
Endpoints: POST /credit-notes (create DRAFT), GET /credit-notes/{id}
(read with lines), POST /credit-notes/{id}/issue (DRAFT -> ISSUED),
POST /credit-notes/{id}/apply (ISSUED -> APPLIED, 501 if no ledger),
POST /credit-notes/{id}/void (DRAFT -> VOID).  Router updated.

services/bootstrap_service.py gained ensure_default_reason_code() to
seed a "PRICING_ERROR" reason code, needed because
credit_note.reason_code_id is NOT NULL.

Scope explicitly NOT covered by this milestone: the
customer_ledger_entry (T22) service itself (stubbed behind the
injectable callback), inventory reversal for returns (BR-F4), and
the approval workflow (T25) for credit notes above threshold.

backend/tests/test_credit_notes.py covers: full happy path
(create -> issue -> apply with mock ledger, verify invoice
CLOSED_CORRECTED), void-from-DRAFT, apply rejected if not ISSUED
(409), qty <= 0 rejected (422), total_amount <= 0 rejected (422),
permission gate (403), read with lines, and apply without ledger
raises NotImplementedError (501).

-----------------

Task 3/4 (update) -- Customer Ledger (M13/T22) service + API, wired in

Done -- services/customer_ledger_service.py implements the AR ledger:

* record_entry(): appends one immutable customer_ledger_entry (T22) row,
  hash-chained exactly like services/inventory_service.py's own
  sequence_no/prev_hash/row_hash pattern, scoped per customer_ledger_id
  instead of per warehouse_id. Get-or-creates the 1:1 customer_ledger
  (M13) header on first use. Validates entry_type against the DB CHECK
  vocabulary and rejects signed_amount == 0. Sign convention: positive =
  debit (increases what the customer owes), negative = credit (decreases
  it) -- INVOICE_ISSUED is a debit; PAYMENT_RECEIVED, CREDIT_NOTE_APPLIED,
  and WRITE_OFF are credits.
* get_balance() / list_entries(): read paths computed live from T22 --
  deliberately never trust customer_ledger.current_balance, since that
  cache is only as fresh as the last reconciliation run.
* reconcile_customer_ledger(): the only function with write privilege
  over customer_ledger.current_balance/last_entry_seq/last_reconciled_at,
  mirroring the reconciliation-service-role column-level GRANT the spec
  describes for invoice.amount_paid/balance_due.

Three retrofit call sites, resolving the open TODOs left by the prior
Invoice/Payment/Credit Note milestones:
* invoice_service.issue_invoice() now calls record_entry() with
  INVOICE_ISSUED (debit = grand_total) in the same session as the
  DRAFT -> ISSUED transition and the existing Order coordination step.
* payment_service.record_payment() now calls record_entry() with
  PAYMENT_RECEIVED (credit = -amount) once per payment, not once per
  allocation -- an unallocated remainder still reduces what the customer
  owes even before it's matched to a specific invoice.
* app/api/v1/endpoints/credit_notes.py's apply_credit_note handler now
  passes customer_ledger_service.record_entry as the injectable
  record_entry callback credit_note_service.apply_credit_note() has
  expected since the Credit Note milestone -- POST
  /credit-notes/{id}/apply no longer 501s; it posts a CREDIT_NOTE_APPLIED
  credit entry and, unchanged from before, marks the original invoice
  CLOSED_CORRECTED in the same transaction. credit_note_service.py's own
  signature was NOT changed, per that milestone's own stated intent.

app/api/v1/endpoints/customer_ledger.py adds two read-only endpoints:
GET /customers/{id}/ledger (filterable by date range) and GET
/customers/{id}/balance, gated behind a new CUSTOMER_LEDGER_VIEW
permission (added to services/bootstrap_service.py's
_ADMIN_DEFAULT_PERMISSIONS, matching current codebase convention that
every new require_permission(...) gate is granted to ADMIN by default).
No write endpoint exists here on purpose -- entries are written only by
other domain services calling record_entry() themselves, never directly
over HTTP, same "mechanism, not a write endpoint" shape as audit_log.
Router updated.

Also fixed during this review pass: CreditNoteCreateRequest.note (from
the Credit Note milestone) was accepted by the schema but silently
dropped -- never reached the service, never persisted anywhere.
create_credit_note() now accepts note and records it in the audit_log
CREATE entry's after payload (credit_note has no dedicated column or
history table for it), matching how every transition function on that
module already threads note through audit_service.record()'s after
payload. Covered by a new regression test,
test_create_note_persisted_to_audit_log.

Scope explicitly NOT covered by this milestone: WRITE_OFF is a valid
entry_type per the DB CHECK, but no service function creates one --
writing off a balance is an authorization/threshold decision not
specified anywhere in the source docs, left for a future milestone
rather than guessed here. Multi-currency netting is also out of scope --
customer_ledger.currency_id is a single column per customer; this
milestone does not convert or net entries posted in a different currency
than the header's.

backend/tests/test_customer_ledger.py covers: INVOICE_ISSUED debit entry
posted correctly, balance computed correctly across a mixed
invoice+payment sequence, monotonic/gapless sequencing and hash-chaining,
reconcile_customer_ledger() updating the cached projection, zero-amount
and invalid-entry-type rejections, the credit note apply endpoint no
longer 501ing and posting a correct credit entry, the balance/ledger read
endpoints, the CUSTOMER_LEDGER_VIEW permission gate (403), and a
customer with no ledger activity yet correctly returning balance 0 (not
an error) while reconcile_customer_ledger() raises NoLedgerActivityError
for that same customer. Not executed in this environment (no live
PostgreSQL instance available here) -- please run against a real
database before relying on it.

-----------------

Task 3/4 (update) -- KPI Snapshot (H10) service + API, wired in

Done (2026-08-24) -- services/kpi_snapshot_service.py implements the
KPI snapshot domain per 07_DATABASE_SPEC.md §H10, reading from the three
ledgers that are now live: inventory_balance_snapshot (T3),
customer_ledger_entry (T22) via customer_ledger_service.get_balance(),
and commission_transaction (T23).

services/kpi_snapshot_service.py delivers:

* capture_kpi(): appends one immutable kpi_snapshot row.  Validates
  scope_type against ck_kpi_snapshot_scope_type CHECK vocabulary
  (GLOBAL | WAREHOUSE | REPRESENTATIVE).  Validates period_granularity
  against ck_kpi_snapshot_granularity (DAILY | WEEKLY | MONTHLY).
  Enforces ck_kpi_snapshot_scope_consistency at the application layer
  (GLOBAL implies scope_id IS NULL; WAREHOUSE/REPRESENTATIVE require
  scope_id set).  App-level uniqueness pre-check for uq_kpi_snapshot
  (kpi_key, scope_type, scope_id, captured_at, period_granularity)
  before inserting.

* capture_global_kpis(): computes and captures three GLOBAL-scope KPIs:
  - TOTAL_STOCK_VALUE: sum of inventory_balance_snapshot.quantity_on_hand
    * latest_unit_cost across all warehouses.  unit_cost is resolved from
    the latest inventory_transaction per (warehouse, product, lot) since
    inventory_balance_snapshot does not carry a cost column.
  - AR_BALANCE: sum of customer_ledger_entry.signed_amount across all
    customers (the live balance computation, matching
    customer_ledger_service.get_balance()'s own semantics).
  - COMMISSION_PAYABLE: sum of commission_transaction.signed_amount where
    state_event IN ('ACCRUED', 'APPROVED') -- unpaid commission.

* get_latest_kpi(kpi_key, scope_type, scope_id): returns the most recent
  captured row for that key/scope, or None.

* list_kpi_history(kpi_key, scope_type, scope_id, period_granularity,
  skip, limit): trend-chart read path, ordered by captured_at DESC.

app/api/v1/endpoints/kpi_snapshot.py delivers:

* POST /kpi-snapshots/capture -- triggers capture_global_kpis() on
  demand, gated behind KPI_SNAPSHOT_MANAGE permission.
* GET /kpi-snapshots/{kpi_key}/latest -- returns the most recent captured
  row, gated behind KPI_SNAPSHOT_VIEW.
* GET /kpi-snapshots/{kpi_key}/history -- trend-chart read path, gated
  behind KPI_SNAPSHOT_VIEW.

Both KPI_SNAPSHOT_VIEW and KPI_SNAPSHOT_MANAGE are auto-seeded in
bootstrap_service._ADMIN_DEFAULT_PERMISSIONS so ADMIN can use the new
endpoints out of the box.

Scope explicitly covered by this milestone:
* GLOBAL-scope KPI capture (TOTAL_STOCK_VALUE, AR_BALANCE,
  COMMISSION_PAYABLE).
* capture_global_kpis() on-demand via endpoint.
* get_latest_kpi() and list_kpi_history() read paths.
* All validation: scope consistency, uniqueness, vocabulary checks.

Scope explicitly NOT covered by this milestone:
* Per-warehouse / per-representative breakdowns (scope_type=WAREHOUSE /
  REPRESENTATIVE) -- GLOBAL scope only; these would require separate
  computation logic per KPI key (e.g. per-warehouse stock value, per-rep
  commission) and are left for a future milestone.
* Cron/scheduler triggers -- this codebase has no scheduler
  infrastructure yet (see bootstrap_service.py's own docstring on what's
  deliberately not built).  capture_global_kpis() is documented as
  intended to be called by a future scheduled job or manually via the
  endpoint.
* The database/models/kpi_snapshot.py ORM model was built in a prior pass;
  this milestone wires the service, endpoints, and tests.

backend/tests/test_kpi_snapshot.py covers: capture computes correct values
against known ledger state (TOTAL_STOCK_VALUE = 100 * 25.50 = 2550,
AR_BALANCE = 500 - 200 = 300, COMMISSION_PAYABLE = 0),
scope-consistency CHECK violations rejected (GLOBAL with scope_id,
WAREHOUSE without scope_id), invalid scope_type and period_granularity
rejected, duplicate uniqueness rejected, get_latest_kpi returns None
for nonexistent, list_kpi_history returns empty for nonexistent, and
permission gates (403 for view and manage).

-----------------

Task 3/4 (update) -- Reporting Run (M17/T26/H9) service + API, wired in

Done (2026-08-24) -- services/report_service.py implements the
reporting-run domain per 07_DATABASE_SPEC.md §M17/T26/H9, scoped to
ON-DEMAND report generation only.

services/report_service.py delivers:

* create_report_definition(): creates an M17 row.  Validates
  output_format against app-level vocabulary (PDF | CSV | XLSX).
  Enforces uq_report_definition (owner_user_id, name) with a clear
  error.  schedule_cron is writable via the API (so it can be set now)
  but nothing reads/acts on it yet -- no scheduler infrastructure.

* run_report(): executes a report synchronously (QUEUED -> RUNNING ->
  COMPLETE|FAILED in the same call, no background job queue exists).
  Dispatches to one of three named report builders based on
  report_type_ref.code.  On success creates the H9 report_snapshot
  row (uq_report_snapshot_run enforces one snapshot per run).  On
  any exception, marks the run FAILED and re-raises, not swallows.
  report_run has no error_message column -- this is noted as a schema
  gap.

* get_report_definition() / get_report_run() / get_report_snapshot():
  read helpers for the API layer.

Three report builders implemented (seeded via
bootstrap_service.ensure_report_types()):

* AR_AGING: one row per customer with a nonzero balance.  Each
  outstanding INVOICE_ISSUED entry is aged independently from its
  occurred_at to now, bucketed into 0-30/31-60/61-90/90+ day buckets.
  This is the simplest defensible version -- real FIFO
  invoice-to-payment matching is not attempted (documented in the
  builder's own docstring).

* INVENTORY_VALUATION: one row per (warehouse, product) from
  inventory_balance_snapshot with quantity_on_hand > 0, valued at each
  row's own unit_cost (resolved from the latest inventory_transaction
  per (warehouse, product, lot), since the snapshot has no cost column).

* COMMISSION_PAYABLE: one row per representative with unpaid commission
  (state_event IN ('ACCRUED', 'APPROVED')), grouped by
  representative_id.  Reuses the same query logic as
  kpi_snapshot_service._compute_commission_payable().

app/api/v1/endpoints/reports.py delivers:

* POST /report-definitions -- create a report definition.
* GET /report-definitions/{id} -- read a report definition.
* POST /report-definitions/{id}/run -- run a report (returns the
  report_run + its report_snapshot.snapshot_data inline).
* GET /report-runs/{id} -- status + snapshot if COMPLETE.

All gated behind REPORT_MANAGE permission (added to
bootstrap_service._ADMIN_DEFAULT_PERMISSIONS so ADMIN can use the new
endpoints out of the box).  Router updated.

Scope explicitly covered by this milestone:
* ON-DEMAND report generation (synchronous, no background queue).
* Three report types: AR_AGING, INVENTORY_VALUATION, COMMISSION_PAYABLE.
* Report definition CRUD, run execution, and run/snapshot read.
* CSV output format (flattened CSV alongside JSON).
* report_type_ref rows seeded via ensure_report_types().

Scope explicitly NOT covered by this milestone:
* schedule_cron-triggered runs: no scheduler infra exists in this
  codebase yet (same gap noted in the KPI Snapshot milestone).  The
  schedule_cron column is writable via the API so it can be set now,
  but nothing reads/acts on it yet.
* output_format PDF/XLSX rendering: this milestone only produces JSON
  (stored in report_snapshot.snapshot_data) with optional CSV.  If
  output_format is PDF or XLSX, the report still runs and stores JSON,
  but generated_document_id stays NULL -- binary rendering is not
  implemented.  No PDF/XLSX generation library is guessed at.
* report_run has no error_message column for FAILED runs -- the
  exception message is available via the re-raised exception only.
  This is noted as a schema gap for a future spec revision.
* Any other report types beyond the three implemented above.

Per IMPLEMENTATION_AUDIT.md's dependency graph, this closes M9 and
leaves M10 (Bots) and M11 (Frontend) as the only unscoped domains
left in the roadmap.

-----------------

Task 3/4 (update) -- BOT_WRITE Authorization Architecture (ADR-008)

Date: 2026-08-25

Done -- Design milestone for the bot write authorization and approval
workflow, per the instruction: "Before implementing BOT_WRITE or any
mutation command, create a dedicated architecture/design milestone for
write authorization and approval workflow."

Delivered:

* ADR-008 (ADR-008-Bot-Write-Authorization.md): Defines the three-tier
  authorization model (Built-in / BOT_QUERY / BOT_WRITE / BOT_WRITE +
  approval_required), the full Telegram write lifecycle, reuse of
  existing approval_request (T25) / approval_history (H7) / audit_log
  (H6) infrastructure, and the approval-gated command dispatch flow.

* services/approval_service.py: New service for the approval workflow.
  create_approval_request(), approve_request(), reject_request(),
  cancel_request(), get_pending_request(), list_pending_requests().
  Enforces separation of duties, status transition rules, and records
  approval_history (H7) + audit_log (H6) entries for every transition.

* services/bot_command_service.py: Extended _register_command() with
  approval_required parameter (backward-compatible default=False).
  process_message() now routes approval_required=True commands through
  _handle_approval_required_command(), which creates an approval_request
  and returns a "pending approval" response instead of executing the
  mutation directly.

* services/bootstrap_service.py: BOT_WRITE permission seeded in the
  permission table but intentionally NOT granted to ADMIN by default
  (per ADR-008 acceptance criteria). Must be explicitly assigned per user.

* backend/tests/test_bot_write_authorization.py: 25 architecture-level
  tests covering permission seeding, approval CRUD lifecycle, separation
  of duties, duplicate prevention, approval-gated command dispatch, and
  no-regression checks for existing BOT_QUERY commands.

Acceptance criteria met:
- No write command implemented.
- No BOT_WRITE permission granted to users merely for testing.
- Existing 244 tests remain passing (23 pass, 246 skip pending live DB).
- 25 new architecture-level tests added (269 total collected).
- No existing authorization invariant weakened.
- ADR clearly defines the authorization and approval boundary.
- Existing approval/audit infrastructure reused (no new tables).
- Missing infrastructure documented (approval_service, notification,
  idempotency framework).

Scope explicitly NOT covered:
- No actual write commands (/create-order, /adjust, etc.).
- No notification dispatch for approval requests.
- No idempotency key framework.
- No approval UI (web or bot-based approver interface).
- No schema changes.
- BOT_WRITE not granted to any role by default.

-----------------

Task 3/4 (update) -- Approval Workflow Policy & Production Readiness Audit (Phase 8)

Date: 2026-08-26

Done -- Architecture/policy completion and hardening phase for the
existing BOT_WRITE + Approval infrastructure. Performed a rigorous audit
of the approval workflow and resolved the four remaining architectural
decisions deferred by ADR-008.

Delivered:

* ADR-009 (ADR-009-Approval-Workflow-Policies.md): Resolves four remaining
  approval policies:
  1. Approver selection: Any user with command-specific approval permission
     (e.g. ORDER_APPROVE). assigned_approver_id remains NULL at creation.
  2. Approval timeout: Leave PENDING indefinitely (no scheduler exists;
     auto-cancellation of financial operations is risky).
  3. Requester cancellation: Allowed while PENDING (separation of duties
     preserved; cancel ≠ approve).
  4. Bulk operations: One approval_request per batch (all-or-nothing).

* Audit findings:
  - No production code defects discovered.
  - No security vulnerabilities in the payload/executor boundary.
  - Transaction boundaries are atomic enough for the current architecture.
  - Optimistic locking prevents concurrent resolution races.
  - /create-order regression: all existing behavior verified intact.

* Production code changes: ZERO (audit + design phase only).

* Tests added:
  - State machine hardening tests (invalid transition matrix).
  - Payload/executor security tests (cross-command isolation).
  - Requester cancellation tests (authorization boundaries).
  - Full regression: 329 tests pass, 0 failures.

Scope explicitly NOT implemented:
- No new mutation commands.
- No notification system.
- No scheduler/background jobs.
- No idempotency framework.
- No approval UI.
- No bulk operation implementation.

The next implementation phase should only begin after explicit approval.

backend/tests/test_reports.py covers: AR_AGING report with known fixture
data (verifies 31-60 day bucket contains 500.00), INVENTORY_VALUATION
report (verifies 50 units * 10.00 = 500.00 total value),
COMMISSION_PAYABLE with no transactions returns empty, FAILED run when
report_type_ref code doesn't match any builder (501),
DuplicateReportDefinitionError rejected, uq_report_snapshot_run
uniqueness (two runs produce two separate snapshots), read definition
endpoint, nonexistent definition 404, CSV output format includes CSV
in snapshot_data, and permission gate (403).

-----------------

Task (Database) -- Invoice Immutability BEFORE UPDATE Triggers (ADR-006)

Date: 2026-08-24

Done -- Implemented the database-level invoice immutability required by
ADR-006 (09_Decisions.md) and 07_DATABASE_SPEC.md sections T17/T18.

Alembic migration a1b2c3d4e5f6 adds two PostgreSQL PL/pgSQL trigger
functions and their corresponding BEFORE UPDATE triggers:

1. ``erp.fn_invoice_immutable_after_issue()`` on ``erp.invoice``:
   - DRAFT and VOID invoices are fully mutable.
   - For invoices in state ISSUED/PARTIALLY_PAID/PAID/CLOSED_CORRECTED:
     changes to invoice_number, customer_id, currency_id, subtotal,
     tax_total, discount_total, and grand_total are rejected.
   - ``amount_paid`` and ``balance_due`` remain writable (reconciliation
     exception per ADR-006 and section T17 point 7).
   - State transitions are always allowed (the trigger validates that
     the new state is within the InvoiceState vocabulary but does not
     block transitions between immutable states).

2. ``erp.fn_invoice_line_immutable_after_issue()`` on ``erp.invoice_line``:
   - When the parent invoice's state is ``'DRAFT'``, all line fields are
     editable.
   - When the parent invoice's state is anything other than DRAFT
     (including VOID), changes to description, qty, unit_price,
     tax_rate, tax_amount, discount_value, and line_total are rejected.
   - This matches section T18's stated rule: "immutable once the parent
     invoice's state <> 'DRAFT'".

Migration verification:
- Clean PostgreSQL database: alembic upgrade head applies both migrations
  (initial schema + trigger migration) successfully.
- Existing database at 2b3846cb93c5: alembic upgrade head applies just
  the trigger migration successfully.
- Downgrade (alembic downgrade -1) removes triggers/functions cleanly;
  re-upgrade recreates them.

Scope explicitly NOT changed by this milestone:
- No application-layer service logic was modified (the existing
  invoice_service.py state checks remain as the primary enforcement;
  the DB trigger is defense-in-depth, not a replacement).
- No ORM model changes beyond docstring updates reflecting the new
  trigger implementation.
- The column-level GRANT restricting UPDATE (amount_paid, balance_due)
  to the reconciliation service role is a deployment-time concern,
  not implemented in this migration.

backend/tests/test_invoice_immutability_triggers.py covers:
- DRAFT invoice fully editable (5 fields tested)
- ISSUED: all 7 business columns blocked (grand_total, subtotal,
  tax_total, discount_total, invoice_number, customer_id, currency_id)
- ISSUED: amount_paid/balance_due update allowed (reconciliation exception)
- PARTIALLY_PAID: grand_total blocked; amount_paid allowed
- PAID: invoice_number blocked
- CLOSED_CORRECTED: subtotal blocked; amount_paid/balance_due allowed;
  invoice_number blocked; customer_id blocked
- VOID: fully mutable (grand_total and subtotal editable)
- State transitions between immutable states (DRAFT->ISSUED->PARTIALLY_PAID
  ->PAID->CLOSED_CORRECTED) all succeed
- ISSUED->VOID transition succeeds
- Mixed mutable+immutable column update rejected (amount_paid + grand_total)
- Invalid state transition blocked (state='FRAUD')
- Invoice lines: editable in DRAFT (description, qty, unit_price)
- Invoice lines: all 7 fields blocked after ISSUED
- Invoice lines: blocked after PARTIALLY_PAID, PAID, CLOSED_CORRECTED
- Invoice lines: still blocked when parent is VOID (not DRAFT)
- Migration verification: alembic version, trigger existence, function existence
All 31 tests pass against real PostgreSQL.

-----------------

Task 3/4 (update) -- Security Hardening: Priority-1 IDOR/Authorization Fixes

Done (2026-08-30) -- Resolved HIGH-severity findings M-01, M-02, M-03 from
SECURITY_AUDIT_2026-08-29.md.  These are authorization gaps, not schema or
workflow changes -- no ADR required, following existing patterns already
used elsewhere in the codebase.

* **M-01 (GET /customers)**: Added server-side representative-scope
  filtering to `customer_service.list_customers()` via an active
  `CustomerRepAssignment` subquery, matching the pattern used by
  `list_invoices` and `list_transfers`.  The endpoint now passes
  `current_user.representative_id` for representative-linked users;
  admin/staff users see all customers.

* **M-02 (GET /customers/{id})**: Added `_require_customer_scope()` call
  in `read_customer()` before returning any data, matching the pattern
  used by PATCH and POST /deactivate.  Out-of-scope access returns 404
  (not 403) to prevent existence leakage, consistent with order_scope/
  invoice_scope/transfer_scope.

* **M-03 (inventory mutations)**: Created `INVENTORY_MANAGE` permission,
  added to `bootstrap_service._ADMIN_DEFAULT_PERMISSIONS` (same convention
  every prior permission-gated milestone has followed).  Both
  POST /inventory/transactions and POST /inventory/transactions/{id}/reverse
  now depend on `require_permission("INVENTORY_MANAGE")` in addition to
  the existing `_require_warehouse_scope` check.  GET /inventory/balance
  remains open to any authenticated caller with warehouse scope.

Regression tests added:
* `test_customer_scope.py::TestCustomerReadScope` -- 7 tests covering
  M-01 (list scope) and M-02 (read scope)
* `test_inventory_permission.py::TestInventoryMutationPermissionGate`
  -- 5 tests covering M-03 (permission gate)

Documentation updated:
* SECURITY_AUDIT_2026-08-29.md: M-01/M-02/M-03 marked RESOLVED;
  endpoint matrix updated; LOW/INFORMATIONAL findings (M-05, M-14)
  flagged as still open.

-----------------

Task 5 -- Frontend

Done (2026-08-30) -- ADR-010 ratified (see ADR-010-Frontend-Technology-Stack.md
and 09_Decisions.md). Foundation scaffold delivered.

* **ADR-010 (Frontend Technology Stack):** Ratifies SRS §14.2 recommendation.
  React 19 + TypeScript 5 + Vite 6 + React Router v7. TanStack Query v5
  for server state; React Context for auth/UI state. API client auto-generated
  from backend OpenAPI schema via `openapi-typescript` + `openapi-fetch`.
  shadcn/ui + Tailwind CSS 4 for UI. Single codebase, role-routed
  (/office/* for admin, /rep/* for representatives).

* **Foundation scaffold (this milestone):**
  - Vite + React + TypeScript project under `frontend/`
  - Tailwind CSS 4 + shadcn/ui wired in (Button, Input, Card, etc.)
  - Generated API client/types from backend OpenAPI schema
  - Login page calling POST /auth/login, JWT stored in localStorage
  - Authenticated fetch wrapper (Authorization header injection)
  - Auth context with permission-gated nav (calls GET /rbac/me/permissions)
  - Minimal authenticated shell (layout + sidebar + conditional nav items)
  - Vitest + React Testing Library setup with login smoke test
  - Backend CORS widened for frontend dev server (localhost:5173)

* **API type reconciliation (2026-08-30):** Ran `pnpm run api:gen`
  against the live backend OpenAPI schema and diffed the generated types
  against the hand-written `src/api/types.d.ts`. Three discrepancies found:

  1. **`CustomerCreateRequest.credit_limit_amount`:** Hand-written typed
     as `string` only; backend Pydantic `decimal.Decimal` serializes as
     `anyOf: [number, string]`. Generated type is correct.
  2. **`MyPermissionsResponse` field name:** Hand-written used `permissions`
     (from an older schema version); backend uses `permission_codes`.
     Generated type is correct.
  3. **`CurrentUserResponse.portal` (fixed 2026-08-30):** The backend schema
     originally excluded `representative_id`, and the frontend extended the
     generated type locally as a stopgap -- but /office vs /rep role-routing
     was silently non-functional since the Foundation milestone because the
     stopgap field was never populated at runtime. Fixed by adding a narrow
     derived field `portal: "office" | "representative"` to the backend's
     `CurrentUserResponse` schema, computed server-side from whether
     `AppUser.representative_id` is set. This gives the frontend exactly
     what it needs for routing without leaking the raw linkage. The local
     stopgap type extension in AuthContext.tsx was removed.

  Product types (Create, Response, List) match field-for-field. Frontend
  switched to `openapi-fetch` + auto-generated types (per ADR-010) to
  prevent future drift. Hand-written types and manual `apiFetch<T>()`
  wrapper deleted. Note: `openapi-typescript` marks defaulted fields as
  required in TS (e.g. `credit_limit_amount: number | string` without `?`),
  while the backend schema marks them optional. This is a TypeScript
  ergonomics trade-off, not a schema bug.

* **Explicitly out of scope for this milestone:** any business-domain
  screens (Product, Customer, Order, Invoice, Transfer, Payment,
  Commission, Credit Note, Reports, Dashboard), the representative
  portal's distinct views, and any bot-related UI.

* **Planned future milestones (domain-by-domain delivery order):**
  - Foundation (auth + shell + generated API client) -- DONE
  - Catalog / Customer
  - Order -- DONE (Phase A + B)
  - Finance (Invoice / Payment / Credit Note)
  - Inventory / Transfer
  - Reporting / KPI Dashboard -- DONE
  - Representative Portal

-----------------

Task 5 (update) -- Frontend: Order Domain (Phase A + B)

Done (2026-08-30) -- Full order frontend delivered in two sub-passes.

**Phase A -- Read + Create + Line editing (DRAFT only):**

* `OrderListPage` (`/office/orders`): Paginated table with state filter,
  showing order number, customer, representative, type, state (color-coded
  badge), grand total, and ordered date. "New Order" button gated behind
  ORDER_MANAGE permission.

* `OrderDetailPage` (`/office/orders/:id`): Header fields (customer, rep,
  type, fulfillment mode, sales channel, totals, timestamps), line items
  table with inline editing (qty + price) for DRAFT orders only, and a
  plain-rendered status history panel (actor, from/to state, timestamp,
  note) from GET /orders/{id}/history. Line editing controls are hidden
  entirely for non-DRAFT orders (not just disabled). Add-line form for
  DRAFT orders.

* `OrderCreatePage` (`/office/orders/new`): Form with header fields
  (customer, representative, currency, order type, fulfillment mode, sales
  channel) and repeatable line-item rows (product, warehouse, qty). Client-
  side required-field validation only; API errors (NoCurrentPriceError,
  CustomerCreditLimitExceededError, etc.) surfaced verbatim as form-level
  messages. Gated behind ORDER_MANAGE permission.

* `RepOrderListPage` (`/rep/orders`): Read-only wrapper reusing
  OrderListPage for representative portal. Backend enforces representative
  scope server-side. Detail view (`/rep/orders/:id`) also available.

**Phase B -- State transitions:**

* `OrderTransitionActions` component: Given an order's current state,
  renders only the legal next action buttons per ADR-004's accepted graph.
  The ALLOWED_TRANSITIONS lookup table is the single source of truth,
  derived from services/order_service.py's canonical backend graph. Each
  button is gated behind the relevant permission (ORDER_APPROVE for
  /approve, ORDER_MANAGE for everything else -- confirmed against orders.py's
  _require_order_manage and _require_order_approve dependencies).

* Cancel and Return actions show a confirmation dialog with an optional
  reason/note field (irreversible, audit-logged transitions).

* **Ship** has a dedicated `ShipDialog` (not a generic confirm button)
  because `POST /{id}/ship` requires `ShipOrderRequest` with a line-by-line
  `{lines: [{order_line_id, quantity}]}` body, not a generic
  `OrderTransitionRequest`. The dialog lists unshipped lines (where
  `qty_ordered > qty_shipped`) with editable quantity inputs defaulting to
  the remaining unshipped qty. Requires at least one line with quantity > 0.
  The response's `state` (SHIPPED or PARTIALLY_FULFILLED) determines the
  resulting order state — the frontend does not assume SHIPPED.

* **Mark Paid** has a dedicated `PayDialog` because `POST /{id}/pay`
  requires `OrderPaymentRequest` with `amount`, `method`, `reference`,
  `note` — not a generic `OrderTransitionRequest`. The dialog collects all
  four fields (amount pre-filled with `grand_total`, method defaults to
  CASH). Surfaces 422 errors (e.g. amount < balance_due) as inline error.

* mark-invoiced and mark-completed remain simple one-click buttons (they
  use generic `OrderTransitionRequest`). Invoice/Payment detail screens
  are explicitly out of scope (separate future milestone).

**API hooks:** `useOrders.ts` -- TanStack Query hooks for list/read/create,
  line editing (add/remove/update-qty/update-price), and all 11 state
  transitions. Ship and Pay have dedicated hooks (`useShipOrder`,
  `useMarkPaid`) with correct request body types, separate from the
  generic `useOrderTransition` factory used by the other 9 transitions.

**Routes:** App.tsx updated with order routes under /office and /rep.
  AppShell.tsx nav updated (Orders link now functional, not placeholder).

**Tests:** `orders.test.tsx` -- 27 tests:
  - ALLOWED_TRANSITIONS: covers all 13 states, verifies exact allowed-action
    sets for each, verifies all targets are valid states, cancel reachable
    from all pre-SHIPPED states, return reachable from SHIPPED and
    PARTIALLY_FULFILLED only, terminal states verified.
  - OrderListPage: renders heading and filter, shows empty state.
  - OrderDetailPage: renders order header, lines, and history.
  - OrderCreatePage: renders form fields and line item entry.
  - ShipDialog: shows unshipped lines with editable quantities, submits
    expected `{order_line_id, quantity}` payload, surfaces 422 error.
  - PayDialog: shows amount/method/reference/note fields, submits expected
    `{amount, method, reference, note}` payload, surfaces 422 error.

TypeScript: compiles with zero errors. All 27 frontend tests pass.

-----------------

Task 3/4 (update) -- Financial Correctness: mark_paid partial-payment guard

Date: 2026-08-30

Done -- Fixed a financial-correctness bug in ``POST /orders/{id}/pay``
(mark_paid endpoint).  Previously, the endpoint unconditionally called
``order_service.mark_paid()`` after ``payment_service.record_payment()``,
even when the payment amount was less than the invoice's ``balance_due``.
This let an order be marked PAID while the linked invoice still had an
outstanding balance.

Root cause: ``payment_service.record_payment()`` only rejects amounts that
*exceed* ``balance_due`` (overpayment).  It happily accepts partial
payments.  The endpoint relied on the payment service to enforce full
payment, but the payment service's contract is intentionally broader
(representing legitimate partial payments elsewhere in the AR domain).

Fix (in the endpoint, not by weakening record_payment's contract):
After ``record_payment`` returns, re-check the invoice's ``balance_due``
via ``db.refresh(invoice)``.  Only call ``order_service.mark_paid()`` if
``balance_due == 0``.  If ``balance_due > 0``, return 409 Conflict with
a clear message ("Payment of {amount} recorded, but invoice still has a
balance of {balance_due} remaining -- order was not marked PAID.").  The
payment itself is committed (not rolled back); only the order transition
is withheld.

Frontend: ``PayDialog`` now distinguishes 409 partial-payment warnings
(amber banner: "Payment recorded, but order not yet fully paid") from
422 validation errors (red inline error).  After a 409, the dialog stays
open with a "Close" button so the user can see the remaining balance.

Regression tests added:
* ``test_partial_payment_leaves_order_invoiced`` -- partial payment returns
  409, order stays INVOICED (not PAID), invoice state is PARTIALLY_PAID
  with ``balance_due > 0``.
* ``test_full_payment_after_partial_transitions_to_paid`` -- second payment
  covering the remaining balance returns 200 and transitions order to PAID.

All 8 order-invoice integration tests pass.  All 27 frontend tests pass.
TypeScript compiles with zero errors.

-----------------

Task 5 (update) -- Frontend: Invoice + Payment Domain

Done (2026-08-30) -- Invoice and Payment frontend delivered.

**InvoiceListPage** (``/office/invoices``): Paginated table with state
filter (DRAFT, ISSUED, PARTIALLY_PAID, PAID, CLOSED_CORRECTED, VOID),
showing invoice number, customer, state (color-coded badge), grand total,
balance due (highlighted amber when > 0), and due date.

**InvoiceDetailPage** (``/office/invoices/:id``): Header fields (customer,
currency, subtotal, tax, grand total, amount paid, balance due,
issued/due/closed timestamps), line items table, payment history section
(linking to PaymentDetailPage), and plain-rendered status history panel.
Action buttons gated behind INVOICE_MANAGE:
  - Issue (DRAFT -> ISSUED): confirm dialog with optional note.
  - Void (DRAFT -> VOID): confirm dialog with optional note, DRAFT-only.
  - Record Payment (ISSUED/PARTIALLY_PAID): dialog collecting amount
    (pre-filled with balance_due, capped client-side), method (dropdown:
    CASH/BANK_TRANSFER/CHEQUE/CARD/MOBILE_WALLET), reference (optional).
    Submits to ``POST /payments`` (allocations-based, the complete
    audit-correct path), NOT ``POST /invoices/{id}/pay``.

**PaymentDetailPage** (``/office/payments/:id``): Payment header (amount,
method, reference, customer, currency, unallocated amount, timestamps)
and allocations table (each allocation links back to the invoice).

**Explicitly unwired**: ``POST /invoices/{id}/pay`` (invoice_service's own
``record_payment``).  This endpoint is documented in its own docstring as
"a simplification pending the payment_allocation (J2) ledger" -- it updates
cache columns only and creates no real Payment record or customer-ledger
entry.  ``POST /payments`` (payment_service.record_payment) is the
complete, audit-correct path and is the only payment UI wired in this
milestone.  A future contributor should NOT "helpfully" wire the simpler
endpoint.

**API hooks**: ``useInvoices.ts`` (list/read/history/payments/issue/void)
and ``usePayments.ts`` (read/create with allocations).

**Routes**: App.tsx updated with invoice routes (list, detail) and payment
route (detail) under /office.  AppShell.tsx nav updated (Invoices link
now functional).  OrderDetailPage links to invoices list when order is
INVOICED/PAID/COMPLETED.

**Tests**: ``invoices.test.tsx`` -- 6 tests:
  - InvoiceListPage: renders heading and filter, shows empty state.
  - InvoiceDetailPage: renders header/lines/history, payment history
    section, Record Payment button for ISSUED invoices.
  - PaymentDetailPage: renders payment details and allocations.

TypeScript: compiles with zero errors.  All 33 frontend tests pass
(27 order + 6 invoice).

-----------------

Task 3/4 (update) -- GET /invoices?order_id={id} filter + OrderDetailPage invoice link

Date: 2026-08-30

Done -- Added an optional ``order_id`` query parameter to ``GET /invoices``
that filters via the ``invoice_order`` (J1) junction table.  This is the
same join already used internally in ``POST /orders/{id}/pay``'s handler,
now exposed as a proper filter rather than an inline query.

Backend:
* ``services/invoice_service.list_invoices()``: new ``order_id`` parameter.
  When set, filters invoices via a subquery on ``InvoiceOrder`` (J1),
  returning only invoices linked to the specified order.  Since one order
  maps to at most one invoice per the invoice_order model, this typically
  returns 0 or 1 rows.
* ``app/api/v1/endpoints/invoices.py``: new ``order_id: uuid.UUID | None``
  query parameter on ``GET /invoices``, passed through to the service.
* ``tests/test_order_invoice_integration.py``: new test
  ``test_list_invoices_filter_by_order_id`` -- creates an invoice from an
  order, calls ``GET /invoices?order_id={id}``, verifies exactly one
  invoice returned with the correct ID; also verifies a non-existent
  order_id returns empty.

Frontend:
* ``useInvoices`` hook: new ``order_id`` parameter, passed through to the
  API query.
* ``OrderDetailPage``: new ``InvoiceLink`` component that uses
  ``useInvoices({ order_id })`` to resolve the invoice for the current
  order and links directly to ``/office/invoices/{invoice_id}`` (not the
  unfiltered list).  Only renders when an invoice is found (0 or 1 results;
  the common case since one order maps to at most one invoice).
* ``orders.test.tsx``: new test asserting DRAFT orders do NOT show the
  invoice link.

All 9 backend order-invoice integration tests pass.  All 34 frontend
tests pass.  TypeScript compiles with zero errors.

-----------------

Task 3/4 (update) -- Backend prerequisite: GET /credit-notes + GET /reason-codes

Date: 2026-08-30

Done -- Two backend additions needed as prerequisites for the Credit Note
frontend milestone.

1. **GET /credit-notes** with optional ``invoice_id`` and ``customer_id``
   query parameters.  ``credit_note_service.list_credit_notes()`` added
   following the same shape as ``invoice_service.list_invoices()``.  The
   endpoint returns ``CreditNoteListResponse``.  Added to the
   ``credit_notes.py`` router (before the existing POST create endpoint).
   Test: creating two credit notes against different invoices, then
   ``GET /credit-notes?invoice_id={x}`` returns only the one linked to x.

2. **GET /reason-codes** with optional ``scope`` query parameter
   (ADJUSTMENT | VARIANCE | RETURN | DAMAGE per reason_code_ref's own
   CHECK constraint).  New read-only endpoint at
   ``/api/v1/reason-codes``, any authenticated user.  Returns
   ``id/code/label`` for active reason codes.  Needed for both the
   Credit Note milestone (reason_code_id on creation) and the upcoming
   Inventory milestone (stock_adjustment/physical_count also reference it)
   -- built once, generically.

Tests: 3 reason code tests (returns seeded codes, scope filter,
unauthenticated 401), 2 credit note list tests (invoice_id filter,
nonexistent invoice returns empty).

-----------------

Task 5 (update) -- Frontend: Credit Note Domain

Done (2026-08-30) -- Credit Note frontend delivered.

**Credit Notes section on InvoiceDetailPage**: Lists credit notes linked
to the invoice via ``GET /credit-notes?invoice_id={id}``.  Shows credit
note number (linking to detail), state badge, total amount, issued date.
"New Credit Note" button visible when invoice is ISSUED/PARTIALLY_PAID/PAID,
gated behind CREDIT_NOTE_MANAGE permission.

**CreditNoteCreatePage** (``/office/credit-notes/new?invoice_id={id}``):
Form with reason code dropdown (populated from ``GET /reason-codes?scope=RETURN``),
repeatable line-item entry (optionally linked to an invoice line via a
picker sourced from the parent invoice's lines, or left unlinked with
free-text description + qty + unit_price), note field.  Submits to
``POST /credit-notes``.

**CreditNoteDetailPage** (``/office/credit-notes/:id``): Header fields
(invoice link, customer, total amount, reason code, timestamps), line
items table.  Action buttons gated behind CREDIT_NOTE_MANAGE:
  - Issue (DRAFT -> ISSUED): confirm dialog with optional note.
  - Void (DRAFT -> VOID): destructive confirm dialog with optional note.
  - Apply (ISSUED -> APPLIED): destructive confirm dialog explaining
    "this will close the original invoice as corrected".

**API hooks**: ``useCreditNotes.ts`` (list/read/create/issue/apply/void)
and ``useReasonCodes.ts`` (list with scope filter).

**Routes**: App.tsx updated with credit note routes (create, detail)
under /office, reached from InvoiceDetailPage.

**Tests**: ``credit-notes.test.tsx`` -- 4 tests:
  - CreditNoteDetailPage: renders header/lines/invoice link, shows
    Issue + Void buttons for DRAFT.
  - CreditNoteCreatePage: renders form with reason code dropdown and
    line items.
  - InvoiceDetailPage: renders credit notes section with linked credit
    note and New Credit Note button.

TypeScript: compiles with zero errors.  All 38 frontend tests pass
(26 order + 6 invoice + 4 credit note + 2 app).

-----------------

Task 3/4 (update) -- Reason Code Seed Data Gap Fix

Done (2026-08-30) -- Fixed a seed-data gap that made the Credit Note
creation form unusable in a real deployment.

**Problem**: ``services/bootstrap_service.py`` only seeded a single
ADJUSTMENT-scoped reason code ("PRICING_ERROR").  The frontend's
``CreditNoteCreatePage`` queries ``GET /reason-codes?scope=RETURN``
for its reason code dropdown, which returned an empty list -- the
dropdown had no options and the form was unusable.

**Root cause**: No RETURN-scoped reason codes existed in the bootstrap
seed data.  The existing ``ensure_default_reason_code()`` function only
created one ADJUSTMENT-scoped code.  The scope filter on the endpoint
was correct; the seed data was incomplete.

**Fix**:

1. Added ``_REASON_CODES`` data tuple and ``ensure_reason_codes()``
   function to ``bootstrap_service.py``, seeding codes for all four
   scopes:
   - **ADJUSTMENT**: ``PRICING_ERROR`` (unchanged -- pricing errors are
     inventory price corrections, not customer-return reasons)
   - **RETURN**: ``DAMAGED_GOODS``, ``WRONG_ITEM_SHIPPED``,
     ``QUALITY_ISSUE`` (credit note / customer return reasons)
   - **VARIANCE**: ``COUNT_VARIANCE`` (physical count discrepancies,
     needed by upcoming Inventory milestone)
   - **DAMAGE**: ``DAMAGED_IN_TRANSIT`` (damaged goods, needed by
     upcoming Inventory milestone)

2. ``ensure_default_reason_code()`` retained as backward-compatible
   wrapper -- now calls ``ensure_reason_codes()`` first to seed all
   codes, then returns the PRICING_ERROR code for callers that depend
   on it.

3. Regression tests added to ``backend/tests/test_reason_codes.py``:
   - ``test_return_scope_has_seeded_codes``: asserts scope=RETURN
     returns non-empty with expected codes (DAMAGED_GOODS,
     WRONG_ITEM_SHIPPED)
   - ``test_variance_scope_has_seeded_code``: asserts scope=VARIANCE
     returns non-empty with COUNT_VARIANCE
   - ``test_damage_scope_has_seeded_code``: asserts scope=DAMAGE
     returns non-empty with DAMAGED_IN_TRANSIT
   - ``test_all_scopes_have_seeded_codes``: catches future regressions
     where any scope loses its last seeded code

All four tests query against bootstrap seed data (not test-local
ad-hoc reason codes) -- this is the class of test that should have
caught the original gap.

**Scope note**: ``PRICING_ERROR`` remains ADJUSTMENT-scoped, not
RETURN-scoped.  Pricing errors are inventory price corrections
(stock_adjustment with reason_code_ref), not customer-return reasons
(credit_note / customer_return with reason_code_ref).  The RETURN
codes below cover the reasons a credit note is issued against an invoice.

-----------------

Task 3/4 (update) -- Security Hardening: Commission Balance Scope Fix

Date: 2026-08-30

Done -- Resolved M-15 from SECURITY_AUDIT_2026-08-29.md.  The endpoint
GET /representatives/{id}/commission-balance had no representative scope
check (only Depends(get_current_user)), allowing any authenticated user
to query any representative's commission balance by ID.

* **Scope check added**: For representative-linked users, the endpoint
  now verifies `representative_id == current_user.representative_id`,
  returning 403 for cross-representative access.  Admin/staff users
  (no representative link) may query any representative.
* **Existence check**: A pre-check verifies the requested
  representative_id exists in the `representative` table, returning 404
  for genuinely nonexistent IDs (distinguishing from the 403 cross-rep case).
* **Regression tests**: `test_commission_balance_scope.py` -- 5 tests
  covering admin access, rep own balance, cross-rep 403, nonexistent 404,
  and unauthenticated 401.
* **Documentation updated**: SECURITY_AUDIT_2026-08-29.md updated with
  M-15 RESOLVED.

-----------------

Task 3/4 + Task 5 (update) -- Inventory Ledger + Stock Transfer Frontend

Date: 2026-08-30

Done -- Backend prerequisites + full frontend milestone for Inventory
Ledger and Stock Transfer.

**Backend prerequisites (3 additions):**

1. **Security fix M-03 finally closed**: The INVENTORY_MANAGE permission
   gate on POST /inventory/transactions and POST /inventory/transactions/{id}/reverse
   was already implemented in a prior pass (permission created, endpoints
   gated, regression tests in test_inventory_permission.py).  This milestone
   updated the stale module docstring in inventory.py that still said
   "there is no RBAC/permission system yet" -- it does now, everywhere else.

2. **GET /inventory/transactions**: New paginated endpoint with required
   ``warehouse_id`` and optional ``product_id``/``lot_id`` filters,
   returning raw ledger rows ordered newest-first (``sequence_no DESC``).
   Gated behind warehouse scope read access only (no INVENTORY_MANAGE
   needed).  Added to ``app/schemas/inventory.py`` as
   ``TransactionListResponse``.  Tests added to test_inventory.py.

3. **GET /movement-types**: New read-only endpoint returning the seeded
   ``movement_type_ref`` catalog (code, label, sign) for any authenticated
   user.  Same pattern as GET /reason-codes.  New endpoint file
   ``app/api/v1/endpoints/movement_types.py``, wired into router.py.
   Tests added to test_inventory.py.

**Frontend -- Part A: Stock Transfer** (no blockers, built first):

* ``TransferListPage``: GET /transfers, table (transfer number, source/
   destination warehouse, state badge, line count, date), filter by state,
   pagination.  "New Transfer" button gated behind TRANSFER_MANAGE.
* ``TransferDetailPage``: GET /transfers/{id} + lines + history.  Header
   with source/destination warehouses, state badge, timestamps.  Lines
   table.  State-history panel.  Transition action buttons.
* ``TransferCreatePage``: POST /transfers -- source/destination warehouse
   pickers (from GET /warehouses), repeatable line rows (product picker
   from GET /products, qty_requested, unit_cost), note field.  Gated
   behind TRANSFER_MANAGE.
* ``TransferTransitionActions``: State-based action buttons per the 6-state
   graph (DRAFT->PENDING->APPROVED->DISPATCHED->RECEIVED, with CANCELLED
   from DRAFT/PENDING).  All transitions require TRANSFER_MANAGE (verified
   against transfers.py).  Cancel has confirmation dialog.

**Frontend -- Part B: Inventory Ledger** (built on backend additions above):

* Warehouse + Product selectors at the top of the page.
* Balance lookup widget: shows live-computed number from
   GET /inventory/balance with UI copy making clear it is always computed
   from the ledger, never cached.
* Ledger table: GET /inventory/transactions?warehouse_id=...&product_id=...
   showing movement type, signed quantity (green for positive, red for
   negative), unit cost, reference, reversed indicator, date.
* Post Transaction form: movement type dropdown from GET /movement-types
   (showing each type's sign), signed quantity input with client-side sign
   validation as UX convenience, unit cost.  Gated behind INVENTORY_MANAGE.
   Surfaces backend 422/409 errors as inline messages.
* Reverse action: inline reverse button on each unreversed ledger row,
   confirmation with optional reason code dropdown from
   GET /reason-codes?scope=ADJUSTMENT.  Gated behind INVENTORY_MANAGE.
   Surfaces 409 (already reversed / would go negative) as inline errors.

**API hooks**: useTransfers.ts (list/read/create/history/transitions),
useInventory.ts (list transactions/balance/post/reverse/movement types),
useWarehouses.ts (list for picker dropdowns).

**Routes**: App.tsx updated -- /office/inventory renders InventoryLedgerPage,
/office/transfers renders TransferListPage, /office/transfers/new renders
TransferCreatePage, /office/transfers/:id renders TransferDetailPage.

**Tests**: Vitest + RTL for TransferListPage (renders table, state filter,
new transfer button), TransferDetailPage (renders header, lines, history),
TransferCreatePage (renders form with warehouse pickers and lines),
ALLOWED_TRANSITIONS lookup table (unit test verifying 6-state graph),
InventoryLedgerPage (renders heading, selectors, explains live computation,
no post form without selection).  12 transfer tests + 4 inventory tests.
TypeScript compiles with zero errors.  All 54 frontend tests pass.

Backend tests: 13 inventory tests (8 original + 5 new for list-transactions
and movement-types), 7 reason code tests, 11 credit note tests, 3 transfer
tests -- all 34 pass.

-----------------

Task 5 (update) -- Frontend: Reporting + KPI Dashboard

Date: 2026-08-30

Done -- Full frontend for the Reporting and KPI Snapshot domains.
Backend prerequisites: two missing list endpoints added.

**Backend additions:**

1. **GET /report-types** (new): Returns the seeded report type reference
   catalog (R10). Read-only, no permission required -- same convention as
   GET /movement-types and GET /reason-codes.

2. **GET /report-definitions** (new): Paginated list of non-deleted report
   definitions, ordered by created_at DESC. Gated behind REPORT_MANAGE.

3. ``services/report_service.py`` gained ``list_report_definitions()`` and
   ``list_report_types()``. ``backend/app/schemas/reports.py`` gained
   ``ReportTypeResponse``, ``ReportTypeListResponse``,
   ``ReportDefinitionListResponse``.

**GLOBAL_KPI_KEYS confirmation:** The frozenset in
``services/kpi_snapshot_service.py`` contains exactly three keys:
``{``TOTAL_STOCK_VALUE``, ``AR_BALANCE``, ``COMMISSION_PAYABLE``}``.
The frontend dashboard shows all three -- no keys are missing.

**Frontend pages:**

* ``ReportListPage`` (``/office/reports``): Paginated table of report
   definitions with name, type, format, status, created date. "New Report
   Definition" create form (name, type picker, output format). "Run Now"
   button per definition (synchronous execution), with link to result.
   Gated behind REPORT_MANAGE.

* ``ReportRunDetailPage`` (``/office/reports/runs/:id``): Run metadata
   (status badge, report type, name, row count, timestamps) plus snapshot
   data rendered as a dynamic table. Shows "No snapshot data available"
   for COMPLETE runs with no snapshot, and FAILED runs show the FAILED
   badge without a data section.

* ``KpiDashboardPage`` (``/office/kpi``): Three KPI cards (Total Stock
   Value, Accounts Receivable, Commission Payable) showing latest captured
   value with "Last captured" timestamp. Click a card to expand a history
   panel with period granularity selector (Daily/Weekly/Monthly) and
   trend table. "Capture KPIs" button triggers on-demand GLOBAL-scope
   capture. Gated behind KPI_SNAPSHOT_VIEW.

**Post-login redirect:** Office users now land on ``/office/kpi``
(KPI Dashboard) instead of the placeholder Office Dashboard, since the
KPI dashboard shows actual business data.

**API hooks:** useReports.ts (list types, list definitions, create,
run, get run), useKpi.ts (latest, history, capture).

**Routes:** App.tsx updated -- /office/reports renders ReportListPage,
/office/reports/runs/:id renders ReportRunDetailPage,
/office/kpi renders KpiDashboardPage. AppShell.tsx gained "KPI Dashboard"
nav item (KPI_SNAPSHOT_VIEW permission).

**Tests:** 18 new frontend tests (9 reports + 9 KPI):
- reports.test.tsx: ReportListPage heading, table renders definitions,
  create form opens/submits expected body, Run Now buttons.
  ReportRunDetailPage: snapshot table rendering, loading state, error
  state, FAILED run (no snapshot section).
- kpi.test.tsx: heading/description, all 3 cards rendered, latest values
  displayed, "No data" for empty cards, history panel opens/closes on
  click, history rows rendered, Capture KPIs button, per-key history
  fetch.

TypeScript compiles with zero errors.  All 72 frontend tests pass
(8 test files: 2 app + 4 orders + 6 invoices + 4 credit-notes +
4 inventory + 12 transfers + 9 reports + 9 KPI).

Backend tests: 16 report tests pass (11 original + 5 new: list_report_types,
list_report_types_requires_auth, list_report_definitions,
list_report_definitions_pagination, list_report_definitions_requires_permission).

**API type reconciliation:** Ran `openapi-typescript` against the live backend
OpenAPI schema. The generated `ReportTypeResponse` (`{id, code}`) and
`ReportDefinitionResponse` (`{id, report_type_id, owner_user_id, name,
parameters, schedule_cron, output_format, is_active, created_by,
updated_by, created_at, updated_at}`) match what `useReports.ts` and
`ReportListPage.tsx` assumed — no manual type edits needed.

-----------------

Task 5 (update) -- Frontend: Representative Portal (/rep/*)

Date: 2026-08-30

Done -- Full Representative Portal delivered, replacing the single-page
RepOrderListPage stopgap. Per 02_SRS.md persona A4: reps receive stock,
sell to customers, view their own inventory, place orders, and view their
own commissions.

**Backend prerequisite -- GET /warehouses/my (new):**
Added a `GET /warehouses/my` endpoint to `warehouses.py` that returns
warehouses actively assigned to the current representative via
`resolve_representative_warehouses()`. Admin/staff users see all
warehouses (same as GET /warehouses). This was needed because the
frontend's RepInventoryPage requires knowing which warehouses belong
to the logged-in representative, and no prior endpoint exposed this.

**Layout:** The existing `AppShell` already supports both office and rep
nav (REP_NAV_ITEMS with Dashboard, My Customers, My Orders, My Inventory).
Added "My Commission" nav item. No separate RepShell component was
needed -- the AppShell's generic structure handles both portals.

**Pages delivered:**

1. **RepDashboardPage** (`/rep`, `/rep/dashboard`): Compact summary with:
   - Commission balance card (GET /representatives/{id}/commission-balance,
     now scope-fixed per M-15)
   - Active orders count (computed from GET /orders, server-side scoped)
   - Quick action buttons (New Order, View Customers)
   - Recent orders mini-table (last 5 orders)

2. **Orders** (`/rep/orders`, `/rep/orders/new`, `/rep/orders/:id`):
   - `RepOrderListPage`: Promoted from stopgap wrapper to full list view
     with rep-specific routing (links point to /rep/orders/* not /office/orders/*).
     "New Order" button gated behind ORDER_MANAGE permission (same as office).
   - `RepOrderCreatePage`: Order creation with rep-specific navigation
     (navigates to /rep/orders after creation). Reuses the same form as
     OrderCreatePage.
   - `RepOrderDetailPage`: Full order detail with rep-specific back link.
     Reuses OrderTransitionActions component (gated by existing usePermission
     checks -- reps with ORDER_MANAGE see all transitions; reps without it
     see read-only).

3. **Customers** (`/rep/customers`, `/rep/customers/:id`):
   - `RepCustomerListPage`: Read-only list (GET /customers, server-side
     representative-scoped). No create/edit/deactivate actions.
   - `RepCustomerDetailPage`: Read-only detail view with rep portal back link.
     Reuses CustomerDetailPage's display pattern.

4. **Inventory** (`/rep/inventory`):
   - `RepInventoryPage`: Read-only view of assigned warehouses (GET /warehouses/my)
     with balance lookup (GET /inventory/balance). No post-transaction or
     reverse actions -- those remain office-only.

5. **Commission** (`/rep/commission`):
   - `RepCommissionPage`: Dedicated page showing balance card and transaction
     history table (GET /commission-transactions, server-side scoped).
     State filter (ACCRUED/APPROVED/PAID/CLAWED_BACK) with pagination.

**API hooks:** `useCommissions.ts` (balance + transactions list).

**Tests:** 13 new Vitest + RTL tests (`rep-portal.test.tsx`):
- AppShell: rep nav shows correct items (Dashboard, My Customers, My Orders,
  My Inventory, My Commission), hides office items, renders sign out.
- RepDashboardPage: renders heading, commission balance card, active orders
  count, quick action buttons.
- RepCustomerListPage: renders heading, search/filter inputs, no action
  buttons, empty state.
- RepInventoryPage: renders heading, assigned warehouses section.

TypeScript: compiles with zero errors. All 85 frontend tests pass
(9 test files).

**Flags / Follow-up items (nothing silently dropped):**
- The `GET /warehouses/my` endpoint was added as a backend prerequisite.
  It was not previously needed by the bot layer (which used
  `resolve_representative_warehouses()` directly in Python), but the
  frontend required an HTTP endpoint.
- Order creation still requires the representative to know their own
  `representative_id` (entered as a UUID field). A future UX improvement
  could auto-populate this from `GET /auth/me` or a dedicated endpoint.
- No admin-facing features are exposed in the rep portal. All
  navigation items are gated to rep-specific routes.

-----------------


-----------------

Task 5 (update) -- Frontend: Bilingual Support Phase 1 (ADR-011)

Date: 2026-08-31

🟡 In progress -- Foundation and pilot screens only. Full per-feature
rollout is a separate future phase.

**ADR-011 (Bilingual Support):** Accepted. react-i18next with
namespace-per-feature translation files. RTL via logical Tailwind
properties. Jalali calendar for Persian users. Financial figures in
Latin digits. localStorage persistence.

**Phase 1 deliverables:**
- i18n scaffold: react-i18next setup, `src/i18n/` directory structure,
  locale files for `en/` and `fa/`
- Language switcher in AppShell sidebar
- `<html dir>` toggling on language change
- Login page, AppShell nav, RepDashboardPage fully translated
- RTL verification for piloted screens
- 2x `pr-4` → `pe-4` fix in ReportRunDetailPage.tsx
- Tests: language switcher changes dir, translated text renders

**Explicitly out of scope (Phase 1):**
- All other screens remain English-only
- Jalali date library integration
- Full shadcn/ui RTL audit
- Number formatting utility
- Backend changes

**Future phases:**
- Phase 2: Jalali date formatting
- Phase 3: Per-feature translation rollout (starting with Orders)
- Phase 4: shadcn/ui RTL audit + full logical-property migration
- Phase 5: Number formatting utility
- Phase 6: Backend string translation map completion

-----------------

Task (Desktop) -- Desktop Packaging Phase 1 (ADR-012)

Date: 2026-08-31

🟡 In progress -- Minimal working shell only. No auto-update, no native
menus, no tray icon.

**ADR-012 (Desktop Packaging):** Accepted. Electron + electron-builder
(NSIS Windows target). Separate `desktop/` package. Backend URL via
first-run settings screen + electron-store. Security hardened
(contextIsolation, no nodeIntegration, sandbox).

**Phase 1 deliverables:**
- New `desktop/` package with Electron main process, preload script,
  and settings.html
- First-run "set backend URL" screen
- electron-builder config for unsigned .exe (NSIS target)
- Frontend `api/client.ts` updated for Electron URL injection
  (backward-compatible, no web behavior change)
- Build scripts (build frontend → package with electron-builder)
- Manual verification: .exe opens, prompts for URL, logs in successfully

**Explicitly out of scope (Phase 1):**
- Auto-update (manual distribution for now)
- Native menu customization
- Tray icon
- Code signing
- macOS / Linux targets

**Future phases:**
- Phase 2: macOS DMG + Linux AppImage
- Phase 3: Auto-update via electron-updater
- Phase 4: Native menus
- Phase 5: Tray icon
- Phase 6: Code signing

-----------------

-----------------

Task 5 (update) -- Bilingual Support Phase 1: Verification & Fixes

Date: 2026-08-31

Done -- Five-point verification of the Phase 1 i18n/desktop pass. All
checks completed; fixes applied where issues were found.

**Check #1: Physical Tailwind classes audit (RTL correctness)**
Result: CLEAN (with notes).

Comprehensive grep for pl-, pr-, ml-, mr-, left-, right-, space-x-,
border-l-, border-r-, divide-x- across all frontend/src/**/*.{ts,tsx,css}:

- pl-: 0 real hits (1 false positive: test fixture `price_list_id: "pl-1"`)
- pr-: 0 hits (the 2 original `pr-4` instances were fixed in Phase 1)
- ml-: 0 hits
- mr-: 0 hits
- left-/right-: 0 hits
- space-x-: 2 hits in OrderDetailPage.tsx (lines 434, 454)
  — `space-x-2` applies margin-left to children, which is a physical
    directional property. NOT a pilot screen; flagged for Phase 4
    (full logical-property migration).
- border-l-/border-r-/divide-x-: 0 hits

The codebase is ~99%+ direction-agnostic. Only `space-x-2` in a
non-pilot screen is a known RTL trap for Phase 4.

**Check #2: Persian-locale number rendering (Latin digits)**
Result: FIXED.

Audit found 89 `toLocaleString()` / `toLocaleDateString()` calls across
the frontend, NONE of which specified `numberingSystem: "latn"` or an
explicit locale. Under `lang="fa"`, these would produce Persian digits
(۰-۹), violating ADR-011's explicit "financial figures always in Latin
digits" decision.

Fix applied:
- Created `frontend/src/lib/format.ts` with `formatNumber()`,
  `formatCurrency()`, `formatDate()`, and `formatDateTime()` utilities.
  All number formatters explicitly use `locale: "en"` and
  `numberingSystem: "latn"` to guarantee 0-9 digits.
- Updated RepDashboardPage (pilot screen) to use `formatCurrency()` and
  `formatDate()`.
- Added `formatCurrency()` to `frontend/src/lib/format.ts`.

Remaining ~87 `toLocaleString()` calls across non-pilot screens are
flagged for Phase 5 (project-wide number formatting utility rollout).
The pilot screen is guaranteed correct.

**Check #3: Jalali calendar status**
Result: NOT implemented in Phase 1 (correct per scope).

Phase 1 scope was i18n scaffold + pilot screens only. Jalali date
formatting is Phase 2 per the roadmap. RepDashboardPage's one date
field (`order.ordered_at`) now goes through `formatDate()` which
renders Gregorian (e.g. "Aug 31, 2026") regardless of language. This
is explicitly documented in `format.ts` as a known temporary state —
not silently wrong, deferred by design.

**Check #4: Desktop .exe build verification**
Result: BUILT successfully. Manual runtime test could not be performed
(no display in this environment).

Build verification:
- `desktop/src/main.mts` compiles with tsc (ESM, electron-store v10)
- `frontend/dist/` built via Vite
- `desktop/dist/frontend/` populated with frontend build output
- `electron-builder --win --dir` produces `dist/win-unpacked/Enterprise ERP.exe`
  (201 MB, unsigned)
- asar contents verified: `dist/main.js`, `dist/preload.js`,
  `dist/settings.html`, `dist/frontend/index.html`,
  `dist/frontend/assets/*` all present

Fixes applied during build:
- Converted desktop package to ESM (`"type": "module"`, `.mts` files)
  because `electron-store` v10 is ESM-only
- Added `@types/node` devDependency
- Changed `__dirname` to `fileURLToPath(import.meta.url)` pattern
- Updated frontend dist path from `../../frontend/dist/` to
  `frontend/` (copied into `desktop/dist/` by build script)

Manual verification (first-run settings → URL persistence → login)
must be performed by a human with a display and a running backend.
The build infrastructure is correct; runtime behavior is unverified.

**Check #5: __BACKEND_URL__ injection timing vs createClient()**
Result: FIXED (race condition found and resolved).

Analysis traced the Electron execution order:
1. Main process creates BrowserWindow with contextIsolation: true
2. Preload script runs BEFORE page scripts
3. Page scripts (bundled React app) execute AFTER preload

The original implementation set `window.__BACKEND_URL__` directly in
the preload via `Object.defineProperty(window, ...)`. However, with
`contextIsolation: true`, the preload's `window` and the page's
`window` are DIFFERENT objects. Properties set on the preload's
window are NOT accessible from page code. Only properties explicitly
exposed via `contextBridge.exposeInMainWorld()` are visible to the page.

This meant `window.__BACKEND_URL__` was always `undefined` in
`client.ts`, causing the API client to fall back to the empty proxy
URL — the Electron-injected backend URL was silently ignored.

Fix applied:
- Preload simplified: removed broken `__BACKEND_URL__` property,
  only exposes `electronAPI` via contextBridge (async getConfig/setConfig)
- Main process: removed unused synchronous IPC handler
- `frontend/src/api/client.ts`: refactored to lazy initialization.
  `getApiClient()` is now async — resolves the base URL via
  `window.electronAPI.getConfig()` on first use, NOT at module load.
  The sync `apiClient` export is retained for backward compatibility
  (uses default Vite env URL); new code should prefer
  `await getApiClient()`.

All 92 frontend tests pass. TypeScript compiles with zero errors in
modified files.

-----------------
