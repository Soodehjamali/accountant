# Security + Correctness Hardening Audit — 2026-08-29

## Phase 1: Test Suite Health

### Investigation Summary

| # | Test File | Failures | Root Cause | Fix |
|---|-----------|----------|------------|-----|
| 7 | `test_cancel_order_reversal.py` | ImportError: `ShipmentInput` | Already fixed by prior security cycle. All 8 tests now pass. | None needed |
| 2 | `test_commission_scope.py` | `22.5000 != 30.0000` | Already fixed by prior security cycle. All 4 tests now pass. | None needed |
| 1 | `test_reservation_concurrency.py` | Both threads get RESERVED | **Test infrastructure bug**. `reserve_order_stock()` transitions to BACKORDERED without raising an exception, but the test used `except Exception` as the signal for BACKORDERED. Production code is correct — advisory lock properly serializes concurrent reservations. | Test fixed |

### Fix Applied
- `test_reservation_concurrency.py`: Changed `reserve_order` inner function to check `order.state` after commit instead of relying on exception/no-exception as BACKORDERED signal.

### Full Suite Result
```
810 passed in 534.30s — 0 failures
```

---

## Phase 2: Complete Endpoint Authorization Matrix

### Authentication Model
- **`get_current_user`**: Extracts Bearer token → verifies signature/expiry → loads AppUser → checks ACTIVE/not-deleted
- **`require_permission(code)`**: Factory returning dependency that checks RBAC permission
- **Scope dependencies**: `_require_order_scope`, `_require_invoice_scope`, `_require_credit_note_scope`, `_require_customer_scope`, `_require_payment_scope`, `_require_transfer_scope`, `_require_warehouse_scope`

### Endpoint Matrix (from actual source code)

| Method | Path | Auth | Permission | Scope | Scope Before Mutation | Notes |
|--------|------|------|-----------|-------|----------------------|-------|
| POST | `/auth/login` | None | None | N/A | N/A | Public endpoint |
| GET | `/auth/me` | Yes | None | N/A | N/A | |
| POST | `/rbac/roles` | Yes | RBAC_MANAGE | N/A | N/A | Global |
| GET | `/rbac/roles` | Yes | None | N/A | N/A | |
| POST | `/rbac/permissions` | Yes | RBAC_MANAGE | N/A | N/A | Global |
| GET | `/rbac/permissions` | Yes | None | N/A | N/A | |
| POST | `/rbac/roles/{rc}/permissions/{pc}` | Yes | RBAC_MANAGE | N/A | N/A | Global |
| POST | `/rbac/users/{uid}/roles` | Yes | RBAC_MANAGE | N/A | N/A | Global |
| DELETE | `/rbac/users/{uid}/roles/{rc}` | Yes | RBAC_MANAGE | N/A | N/A | Global |
| GET | `/rbac/me/permissions` | Yes | None | N/A | N/A | |
| POST | `/orders` | Yes | ORDER_MANAGE | rep check (body.representative_id == user.representative_id) | ✅ Before service call | |
| GET | `/orders` | Yes | None | Server-side rep filter | ✅ Query-level | |
| GET | `/orders/{id}` | Yes | None | order_scope | ✅ Via dependency | |
| GET | `/orders/{id}/lines` | Yes | None | order_scope | ✅ Via dependency | |
| GET | `/orders/{id}/history` | Yes | None | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/submit` | Yes | ORDER_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/approve` | Yes | ORDER_APPROVE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/reserve` | Yes | ORDER_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/resubmit` | Yes | ORDER_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/cancel` | Yes | ORDER_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/start-fulfillment` | Yes | ORDER_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/ship` | Yes | ORDER_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/return` | Yes | ORDER_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/invoice` | Yes | ORDER_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/pay` | Yes | ORDER_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/complete` | Yes | ORDER_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/orders/{id}/commission` | Yes | COMMISSION_MANAGE | order_scope | ✅ Via dependency | |
| POST | `/invoices/from-order` | Yes | INVOICE_MANAGE | order_scope (pre-check) | ✅ Before service call | |
| GET | `/invoices` | Yes | None | Server-side rep filter | ✅ Query-level | |
| GET | `/invoices/{id}` | Yes | None | invoice_scope | ✅ Via helper call | |
| GET | `/invoices/{id}/lines` | Yes | None | invoice_scope | ✅ Via helper call | |
| GET | `/invoices/{id}/history` | Yes | None | invoice_scope | ✅ Via helper call | |
| POST | `/invoices/{id}/issue` | Yes | INVOICE_MANAGE | invoice_scope | ✅ Via dependency | |
| POST | `/invoices/{id}/pay` | Yes | INVOICE_MANAGE | invoice_scope | ✅ Via dependency | |
| POST | `/invoices/{id}/void` | Yes | INVOICE_MANAGE | invoice_scope | ✅ Via dependency | |
| POST | `/payments/payments` | Yes | PAYMENT_MANAGE | customer_scope (pre-check) | ✅ Before service call | |
| GET | `/payments/payments/{id}` | Yes | None | payment_scope | ✅ Via helper call | |
| GET | `/payments/invoices/{id}/payments` | Yes | None | invoice_scope | ✅ Via helper call | |
| POST | `/transfers` | Yes | TRANSFER_MANAGE | warehouse_scope (inline check) | ✅ Before service call | |
| GET | `/transfers` | Yes | None | Server-side rep filter | ✅ Query-level | |
| GET | `/transfers/{id}` | Yes | None | transfer_scope | ✅ Via dependency | |
| GET | `/transfers/{id}/lines` | Yes | None | transfer_scope | ✅ Via dependency | |
| GET | `/transfers/{id}/history` | Yes | None | transfer_scope | ✅ Via dependency | |
| POST | `/transfers/{id}/dispatch` | Yes | TRANSFER_MANAGE | transfer_scope | ✅ Via dependency | |
| POST | `/transfers/{id}/receive` | Yes | TRANSFER_MANAGE | transfer_scope | ✅ Via dependency | |
| POST | `/transfers/{id}/cancel` | Yes | TRANSFER_MANAGE | transfer_scope | ✅ Via dependency | |
| POST | `/customers` | Yes | CUSTOMER_MANAGE | None (global) | N/A | Master data |
| GET | `/customers` | Yes | None | **NONE** | N/A | **FINDING: M-01** |
| GET | `/customers/{id}` | Yes | None | **NONE** | N/A | **FINDING: M-02** |
| PATCH | `/customers/{id}` | Yes | CUSTOMER_MANAGE | customer_scope | ✅ Before mutation | |
| POST | `/customers/{id}/deactivate` | Yes | CUSTOMER_MANAGE | customer_scope | ✅ Before mutation | |
| GET | `/customers/{id}/ledger` | Yes | CUSTOMER_LEDGER_VIEW | customer_scope | ✅ Before data return | |
| GET | `/customers/{id}/balance` | Yes | CUSTOMER_LEDGER_VIEW | customer_scope | ✅ Before data return | |
| POST | `/customers/{id}/ledger/reconcile` | Yes | CUSTOMER_LEDGER_MANAGE | customer_scope | ✅ Before mutation | |
| POST | `/inventory/transactions` | Yes | **NONE** | warehouse_scope | ✅ Before mutation | **FINDING: M-03** |
| POST | `/inventory/transactions/{id}/reverse` | Yes | **NONE** | warehouse_scope | ✅ Before mutation | **FINDING: M-03** |
| GET | `/inventory/balance` | Yes | None | warehouse_scope | ✅ Before data return | |
| POST | `/products` | Yes | PRODUCT_MANAGE | None (global) | N/A | Master data |
| GET | `/products` | Yes | None | None (global) | N/A | Master data |
| GET | `/products/{sku}` | Yes | None | None (global) | N/A | Master data |
| POST | `/report-definitions` | Yes | REPORT_MANAGE | None | N/A | |
| GET | `/report-definitions/{id}` | Yes | REPORT_MANAGE | None | N/A | |
| POST | `/report-definitions/{id}/run` | Yes | REPORT_MANAGE | None | N/A | |
| GET | `/report-runs/{id}` | Yes | REPORT_MANAGE | None | N/A | |
| POST | `/kpi-snapshots/capture` | Yes | KPI_SNAPSHOT_MANAGE | None | N/A | Global |
| GET | `/kpi-snapshots/{key}/latest` | Yes | KPI_SNAPSHOT_VIEW | None | N/A | Global |
| GET | `/kpi-snapshots/{key}/history` | Yes | KPI_SNAPSHOT_VIEW | None | N/A | Global |
| GET | `/audit-log` | Yes | AUDIT_LOG_VIEW | None | N/A | Global |
| GET | `/audit-log/{id}` | Yes | AUDIT_LOG_VIEW | None | N/A | Global |
| GET | `/commission-configs` | Yes | None | None | N/A | Global |
| POST | `/commission-configs` | Yes | COMMISSION_MANAGE | None | N/A | Global |

### Ownership Chains
| Entity | Chain |
|--------|-------|
| Order | `Order.representative_id → Representative` |
| Invoice | `Invoice → InvoiceOrder → Order → Representative` |
| CreditNote | `CreditNote → Invoice → InvoiceOrder → Order → Representative` |
| Payment | `Payment → Customer → CustomerRepAssignment → Representative` |
| StockTransfer | `StockTransfer → Warehouse → WarehouseAssignment → Representative` |
| Customer | `Customer → CustomerRepAssignment → Representative` |
| Warehouse | `Warehouse → WarehouseAssignment → Representative` |

---

## Phase 3: IDOR / BOLA Findings

### FINDING M-01: Customer List Endpoint — No Representative Scope Filtering
- **Endpoint**: `GET /api/v1/customers`
- **Vulnerability**: Any authenticated user can list ALL customers regardless of their representative assignment
- **Attack scenario**: Representative A calls `GET /api/v1/customers` — sees all customers including those belonging only to Representative B
- **Affected role**: Representative-linked users
- **Affected data**: Customer names, codes, tax numbers, credit limits, billing addresses
- **Business impact**: HIGH — cross-representative customer data leakage; competitor intelligence
- **Root cause**: `list_customers()` endpoint does not pass `representative_id` to the service query. The service function has no representative filtering at all.
- **Recommended fix**: Add server-side representative scope filtering to `list_customers` similar to how `list_orders`, `list_invoices`, and `list_transfers` do it. For representative-linked users, only return customers that have an active `CustomerRepAssignment` to their representative.
- **Test required**: Yes — representative-linked user must not see other reps' customers

### FINDING M-02: Customer Get Endpoint — No Representative Scope Check
- **Endpoint**: `GET /api/v1/customers/{customer_id}`
- **Vulnerability**: Any authenticated user can read any customer by ID, even customers they are not assigned to
- **Attack scenario**: Representative A calls `GET /api/v1/customers/{representative_b_customer_id}` — sees full customer details
- **Affected role**: Representative-linked users
- **Affected data**: Full customer profile (name, code, currency, tax number, credit limit, billing address)
- **Business impact**: HIGH — cross-representative customer data exposure
- **Root cause**: `read_customer()` endpoint uses only `get_current_user` without calling `_require_customer_scope`. The scope check exists for `PATCH` and `POST /deactivate` but not for `GET`.
- **Recommended fix**: Add `_require_customer_scope` call before returning customer data in `read_customer()`. Note: this should raise 404 for out-of-scope customers (not 403) to prevent existence leakage.
- **Test required**: Yes — representative must get 404 for other reps' customers

### FINDING M-03: Inventory Endpoints — No Permission Required for Financial Mutations
- **Endpoint**: `POST /api/v1/inventory/transactions`, `POST /api/v1/inventory/transactions/{id}/reverse`
- **Vulnerability**: Any authenticated user with warehouse scope can post inventory transactions and reverse them — no `INVENTORY_MANAGE` or similar permission is required
- **Attack scenario**: A user with only `ORDER_MANAGE` permission who happens to have warehouse access can manipulate inventory quantities, post fake receipts, or reverse legitimate transactions
- **Affected role**: Any authenticated user with warehouse assignment
- **Affected data**: Inventory quantities (financial implications via unit_cost × signed_quantity)
- **Business impact**: HIGH — uncontrolled inventory manipulation; financial statement integrity risk
- **Root cause**: `inventory.py` endpoints only depend on `get_current_user` + `_require_warehouse_scope` but do not require a specific permission. The inventory endpoints are the only mutating endpoints in the system without an explicit permission gate.
- **Recommended fix**: Create `INVENTORY_MANAGE` permission and gate mutating inventory endpoints behind it. Keep `GET /inventory/balance` open to any authenticated user with warehouse scope.
- **Test required**: Yes — user without INVENTORY_MANAGE must get 403

---

## Phase 4: Business Logic Authorization Findings

### FINDING M-04: Inventory Posting — No Negative Stock Enforcement at API Level
- **Endpoint**: `POST /api/v1/inventory/transactions`
- **Vulnerability**: The service layer does enforce no-negative-stock (returns `NegativeStockError` → 409), but the endpoint's error map translates this to 409 (Conflict). This is correct but should be documented as a deliberate design choice.
- **Classification**: FALSE POSITIVE — the enforcement exists and works correctly.
- **Status**: Verified by code review

### FINDING M-05: Invoice Create From Order — Customer Scope Not Verified
- **Endpoint**: `POST /api/v1/invoices/from-order`
- **Vulnerability**: The endpoint checks `_require_order_scope` but does NOT verify that the order's customer is within the caller's customer scope. A representative who owns an order can create an invoice even if they are not assigned to the order's customer.
- **Attack scenario**: Representative A is assigned to Order X which belongs to Customer Y (which is assigned to Representative B). Representative A can create an invoice for this order.
- **Business impact**: LOW — the order scope already implies legitimate access, and the invoice would be linked to the order's customer regardless. The order ownership chain is the primary authorization.
- **Classification**: LOW / INFORMATIONAL — the order scope is the controlling authorization; customer scope is an additional layer that could be added for defense-in-depth.

---

## Phase 5: Mass Assignment / Input Tampering Findings

### Schema Review Summary

All POST/PATCH request schemas use explicit field declarations (Pydantic `BaseModel`), not `model_dump()`. This provides inherent mass assignment protection — only declared fields are accepted.

| Endpoint | Schema | Fields Acceptable | Server-Controlled Fields | Assessment |
|----------|--------|-------------------|-------------------------|------------|
| POST /orders | `OrderCreateRequest` | customer_id, representative_id, currency_id, order_type, fulfillment_mode, sales_channel, lines | status, grand_total, subtotal, order_number, created_by | ✅ Safe |
| PATCH /customers | `CustomerUpdateRequest` | name, city_ref_id, billing_address, credit_limit_amount, tax_number, status | code, type, currency_id, created_by | ✅ Safe |
| POST /invoices/from-order | `InvoiceCreateFromOrderRequest` | order_id, due_days, note | grand_total, state, customer_id, currency_id | ✅ Safe |
| POST /payments/payments | `PaymentCreateRequest` | customer_id, currency_id, amount, method, reference, received_at, allocations | unallocated_amount, received_by, payment_number | ✅ Safe |
| POST /credit-notes | `CreditNoteCreateRequest` | invoice_id, reason_code_id, lines, reference_type, reference_id, note | total_amount, state, customer_id, issued_by | ✅ Safe |
| POST /inventory/transactions | `PostTransactionRequest` | product_id, warehouse_id, movement_type_code, signed_quantity, unit_cost, currency_id, lot_id, reason_code_id, reference_type, reference_id | sequence_no, row_hash, prev_hash, is_reversed | ✅ Safe |
| POST /transfers | `TransferCreateRequest` | source_warehouse_id, destination_warehouse_id, lines, note | state, requested_by, approved_by, dispatch/receive timestamps | ✅ Safe |
| POST /commission-configs | `CommissionConfigCreateRequest` | rate, effective_from, effective_to, representative_id, product_category_id, order_type | created_by, all audit fields | ✅ Safe |
| POST /products | `ProductCreateRequest` | sku, name, description, base_uom_id, category_id | status, is_lot_tracked, is_serial_tracked, is_perishable, created_by | ✅ Safe |
| POST /rbac/roles | `RoleCreateRequest` | code, name, description | created_by | ✅ Safe |
| POST /rbac/permissions | `PermissionCreateRequest` | code, name, resource, action | created_by | ✅ Safe |

### FINDING M-06: `created_by` Field Set from `current_user.id` Correctly
- All endpoints that create records set `created_by=current_user.id` from the authenticated user, not from the request body.
- **Classification**: FALSE POSITIVE — properly enforced.

### FINDING M-07: Order `representative_id` Scope Check
- **Endpoint**: `POST /api/v1/orders`
- The endpoint checks `body.representative_id != current_user.representative_id` for representative-linked users.
- This prevents a representative from creating orders for other representatives.
- **Classification**: FALSE POSITIVE — properly enforced.

---

## Phase 6: Financial Integrity Findings

### FINDING M-08: Invoice Payment — No Idempotency Check for Duplicate Payments
- **Endpoint**: `POST /api/v1/invoices/{id}/pay`
- **Vulnerability**: The service layer does not check for duplicate payment amounts or duplicate payment timestamps. If the same request is submitted twice (e.g., due to network retry), two separate payment ledger entries and customer ledger entries would be created.
- **Attack scenario**: User submits `POST /invoices/{id}/pay` with amount=100 twice → two 100-unit payments recorded → invoice overpaid or customer balance incorrect
- **Affected role**: Any user with INVOICE_MANAGE permission
- **Affected data**: Invoice amount_paid, customer ledger entries, customer balance
- **Business impact**: MEDIUM — financial overstatement or understatement depending on direction
- **Root cause**: No idempotency key or deduplication mechanism for payment recording
- **Recommended fix**: Add an idempotency key mechanism or at minimum check for duplicate payment amounts within a time window. Alternatively, ensure the client uses idempotency keys.
- **Test required**: Yes — verify duplicate payment submission behavior

### FINDING M-09: Commission Calculation — No Deduplication
- **Endpoint**: `POST /api/v1/orders/{id}/commission`
- **Vulnerability**: The service does not check if a commission transaction already exists for the given order. Multiple submissions could create duplicate commission transactions.
- **Attack scenario**: User submits commission calculation twice → two commission transactions for the same order → double-counted commission payable
- **Affected role**: Any user with COMMISSION_MANAGE permission
- **Affected data**: Commission transactions, KPI snapshots
- **Business impact**: MEDIUM — financial overstatement of commission payable
- **Root cause**: `calculate_commission_for_order()` does not check for existing commission on the order
- **Recommended fix**: Add a uniqueness check before creating commission transaction. Either add a unique constraint on `order_id` in `commission_transaction` table, or check in the service layer before inserting.
- **Test required**: Yes — verify duplicate commission creation behavior

### FINDING M-10: Credit Note Application — No Double-Application Check
- **Endpoint**: `POST /api/v1/credit-notes/{id}/apply`
- **Vulnerability**: The endpoint pre-checks `cn.state != "ISSUED"` but does not check if the credit note has already been applied. The service layer's `apply_credit_note` may handle this, but the double state check (endpoint + service) could mask race conditions.
- **Classification**: LOW — the state machine (ISSUED → APPLIED) prevents double-application since the state would already be APPLIED after first application.
- **Status**: Verified — state machine is the protection mechanism

---

## Phase 7: Concurrency / Idempotency Findings

### FINDING M-11: Reservation Race Condition — PROPERLY MITIGATED
- The `pg_advisory_xact_lock` + `SELECT ... FOR UPDATE` mechanism properly serializes concurrent reservation attempts.
- **Classification**: FALSE POSITIVE — advisory lock works correctly.

### FINDING M-12: Payment Recording — No Concurrency Protection for Invoice Balance
- **Endpoint**: `POST /api/v1/invoices/{id}/pay`
- **Vulnerability**: Two concurrent payment requests for the same invoice could both read the same `amount_paid` value and both succeed, resulting in overpayment.
- **Attack scenario**: Two concurrent `POST /invoices/{id}/pay` with amount=50 each, invoice total=80 → both succeed → amount_paid=100 > grand_total=80
- **Affected role**: Any user with INVOICE_MANAGE permission
- **Affected data**: Invoice amount_paid, balance_due, customer ledger
- **Business impact**: MEDIUM — financial overpayment
- **Root cause**: `record_payment` does not use row-level locking or advisory locks on the invoice before checking balance
- **Recommended fix**: Add `SELECT ... FOR UPDATE` on the Invoice row before checking balance in `record_payment`, or use an advisory lock on the invoice_id
- **Test required**: Yes — concurrent payment test

---

## Phase 8: API Enumeration / Information Leakage Findings

### FINDING M-13: Customer Endpoints — Existence Leakage
- **Endpoint**: `GET /api/v1/customers/{customer_id}` (Finding M-02)
- **Vulnerability**: Returns 404 with descriptive error message "Customer not found" for both non-existent and out-of-scope customers. However, for the `read_customer` endpoint, there is NO scope check at all, so all customers are visible.
- **Classification**: INFORMATIONAL — once M-02 is fixed, the 404 behavior should also be unified.

### FINDING M-14: Audit Log — Global Visibility Without Scope
- **Endpoint**: `GET /api/v1/audit-log`, `GET /api/v1/audit-log/{id}`
- **Vulnerability**: The audit log is globally readable (by anyone with AUDIT_LOG_VIEW permission) without any representative scope filtering. A representative could potentially see audit entries for actions taken by other representatives.
- **Attack scenario**: Representative A (with AUDIT_LOG_VIEW) calls `GET /audit-log?entity_type=customer` — sees all customer creation/update audit entries across all representatives.
- **Affected role**: Representative-linked users with AUDIT_LOG_VIEW permission
- **Affected data**: Audit trail metadata (entity_type, entity_id, action, actor_user_id, before/after snapshots)
- **Business impact**: LOW — audit log is administrative in nature, but cross-representative visibility may not be intentional
- **Root cause**: No representative scope filtering on audit log queries
- **Recommended fix**: Add optional representative scope filtering to `audit_service.list_entries()`. This is lower priority since the audit log is an administrative concern.
- **Test required**: Optional

---

## Summary: All Findings

### CRITICAL
None

### HIGH
| ID | Description | Endpoint | Fix Required |
|----|-------------|----------|--------------|
| M-01 | Customer list — no representative scope filtering | `GET /customers` | Add server-side rep filter |
| M-02 | Customer get — no representative scope check | `GET /customers/{id}` | Add `_require_customer_scope` |
| M-03 | Inventory mutations — no permission gate | `POST /inventory/transactions`, `POST /inventory/transactions/{id}/reverse` | Add `INVENTORY_MANAGE` permission |

### MEDIUM
| ID | Description | Endpoint | Fix Required |
|----|-------------|----------|--------------|
| M-08 | Invoice payment — no idempotency check | `POST /invoices/{id}/pay` | Add idempotency mechanism |
| M-09 | Commission calculation — no deduplication | `POST /orders/{id}/commission` | Add uniqueness check |
| M-12 | Invoice payment — no concurrency protection | `POST /invoices/{id}/pay` | Add row-level lock on invoice |

### LOW
| ID | Description | Endpoint | Fix Required |
|----|-------------|----------|--------------|
| M-05 | Invoice create — customer scope not verified | `POST /invoices/from-order` | Optional: add customer scope check |
| M-10 | Credit note apply — double-application check | `POST /credit-notes/{id}/apply` | State machine already protects |

### INFORMATIONAL
| ID | Description | Endpoint | Fix Required |
|----|-------------|----------|--------------|
| M-13 | Customer 404 — existence leakage (pending M-02 fix) | `GET /customers/{id}` | Will be fixed with M-02 |
| M-14 | Audit log — no representative scope filtering | `GET /audit-log` | Optional: add rep filter |

### FALSE POSITIVE
| ID | Description | Reason |
|----|-------------|--------|
| M-04 | Inventory negative stock enforcement | Service layer correctly enforces |
| M-06 | `created_by` field set from `current_user.id` | Correctly enforced everywhere |
| M-07 | Order representative scope on create | Correctly checked in endpoint |
| M-11 | Reservation race condition | Advisory lock works correctly |

---

## Phase 9: Recommended Remediation Order

### Priority 1 (Security — immediate)
1. **M-01 + M-02**: Add representative scope to customer list and get endpoints
2. **M-03**: Add `INVENTORY_MANAGE` permission to inventory mutation endpoints

### Priority 2 (Financial Integrity)
3. **M-08**: Add idempotency to invoice payment recording
4. **M-09**: Add commission deduplication
5. **M-12**: Add concurrency protection to invoice payment

### Priority 3 (Defense in Depth)
6. **M-05**: Optional customer scope check on invoice creation
7. **M-14**: Optional audit log scope filtering

### Test Suite Status
- **810 passed, 0 failures** — 100% pass rate achieved
- 1 test fixed (test_reservation_concurrency.py — test infrastructure bug)
- 9 previously failing tests (7 cancel_order_reversal + 2 commission_scope) resolved by prior security cycle
