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
