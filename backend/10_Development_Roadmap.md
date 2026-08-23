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
