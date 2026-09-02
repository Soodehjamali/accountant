# Accountant — Product Completion Roadmap

## Phase 1 — Core Sales Workflow

Order → Invoice → Payment → Customer Balance / Ledger

**Goal:** A user can complete a real sale entirely through the application.

**Status:** COMPLETE

Backend services: order_service, invoice_service, payment_service, customer_ledger_service
Frontend pages: OrderListPage, OrderDetailPage, OrderCreatePage, InvoiceListPage, InvoiceDetailPage, PaymentListPage, PaymentDetailPage
Full order-to-invoice-to-payment flow is wired and working.

---

## Phase 2 — Inventory Workflow

Initial Stock → Stock In/Out → Transfer → Return → Current Balance

Initial Inventory is already COMPLETE and E2E verified.

**Goal:** Inventory must correctly reflect real business operations.

**Status:** COMPLETE

Backend services: inventory_service, stock_transfer_service
Frontend pages: InventoryLedgerPage, TransferListPage, TransferDetailPage, TransferCreatePage
Initial Inventory E2E verified with real currency UUID.

---

## Phase 3 — Customer Return

Completed Sale → Customer Return → Inventory Adjustment → Financial Reversal → Commission Clawback

**Goal:** Complete the end-to-end return workflow.

**Status:** COMPLETE

Backend: return_service.py with full lifecycle (PENDING_APPROVAL → APPROVED → RECEIVED → INSPECTED → CLOSED/REJECTED)
API: POST /customer-returns, GET /customer-returns, GET /customer-returns/{id}, POST /customer-returns/{id}/receive|inspect|close
Frontend: ReturnListPage, ReturnDetailPage, ReturnCreatePage with state transitions
Commission clawback on return close: IMPLEMENTED (BR-R3: DIRECT order returns trigger clawback)

---

## Phase 4 — Commission Management

Commission Configuration → Transaction → Approve → Pay → Clawback

**Goal:** Commission lifecycle is fully manageable from the admin UI.

**Status:** COMPLETE

Backend: commission_service with full lifecycle
Frontend: CommissionAdminPage with config CRUD, approve, pay, clawback
Smoke verified: full lifecycle + clawback path both pass.

---

## Phase 5 — Accounting & Financial Operations

Customer Ledger → Receivables → Payments → Outstanding Balance → Financial Reversal

**Goal:** Make financial effects of business operations visible and usable from the application.

**Status:** COMPLETE

Backend: customer_ledger_service with record_entry, get_balance, list_entries, reconcile_customer_ledger
API: GET /customers/{id}/ledger, GET /customers/{id}/balance
Frontend: CustomerDetailPage shows balance card + paginated ledger entries with type filter
Sign convention: +debit (invoice issued), -credit (payment received, credit note applied)

---

## Phase 6 — Master Data Completion

- Customers
- Products
- Warehouses
- Sales Representatives
- Currency
- Other essential master data only where required by business workflows.

**Goal:** All required master data can be managed and used in real workflows.

**Status:** COMPLETE

All master data entities have full CRUD pages in both office and representative portals.

---

## Phase 7 — Reports & Dashboard

- Sales
- Receivables
- Customer Balances
- Inventory
- Payments
- Commission

**Goal:** Provide the minimum useful operational reporting.

**Status:** COMPLETE

Backend: report_service with AR_AGING, INVENTORY_VALUATION, COMMISSION_PAYABLE builders
Frontend: ReportListPage, ReportRunDetailPage, KpiDashboardPage

---

## Phase 8 — UX / Product Polish

- Loading states
- Error handling
- Validation
- Empty states
- Notifications
- Navigation
- Localization/formatting consistency

**Goal:** Make the application practically usable and professional.

**Status:** POST-RELEASE / NON-BLOCKING

Bilingual support Phase 1 complete (i18n scaffold, language switcher, pilot screens).
Desktop packaging Phase 1 complete (Electron + NSIS builder).
Remaining UX polish items are non-blocking and may continue post-release.

---

## Phase 9 — Final Acceptance / E2E

Validate the complete real-world business flow:

Customer
→ Product
→ Warehouse
→ Initial Stock
→ Representative
→ Order
→ Invoice
→ Stock Movement
→ Payment
→ Commission
→ Customer Return
→ Inventory Return
→ Financial Reversal
→ Commission Clawback
→ Final Customer Balance

Only execute tests necessary to prove the workflow works.

**Status:** COMPLETE

Golden Path E2E executed and PASSED (2026-09-01).

All major subsystems verified in one realistic scenario:
- Customer created, Product used in sale, Warehouse had opening stock (100 units)
- Order: DRAFT -> PENDING_APPROVAL -> APPROVED -> RESERVED -> FULFILLING -> SHIPPED -> COMPLETED
- Stock decreased correctly: 100 -> 95 after shipment of 5 units
- Invoice: DRAFT -> ISSUED -> PAID
- Payment recorded with customer ledger entry (+500 invoice, -500 payment = 0 balance)
- Commission: 10% rate configured, calculated (50.00), approved, paid
- Customer Return: PENDING_APPROVAL -> APPROVED -> RECEIVED -> INSPECTED -> CLOSED
- Final customer balance: 0.0000 (correct)
- Commission clawback: Not triggered (order was LOCAL, not DIRECT - correct per BR-R3)

---

## Phase 10 — Production / EXE

Production configuration
→ Build
→ Electron packaging
→ Installer / EXE
→ Clean-machine verification
→ Release

**Status:** COMPLETE

Desktop packaging delivered (Electron + electron-builder NSIS target).
Frontend build → electron-builder → unsigned .exe verified.
Release build completed and validated.

---

## Release Candidate

**Status:** PASSED

### Release v0.1.0 (2026-09-01)

- **Build:** `Enterprise ERP Setup 0.1.0.exe` (NSIS installer, Windows x64)
- **Portable:** `win-unpacked/Enterprise ERP.exe`
- **Build configuration:** No dev-only configuration in production build. CORS, secret key, and database URL are environment-configurable.
- **Electron security:** contextIsolation=true, nodeIntegration=false, sandbox=true
- **Configuration persistence:** electron-store saves backend URL to OS app-data directory
- **First-run flow:** Settings page prompts for backend URL on first launch

### White Screen Fix (2026-09-01)

**Root cause:** Two issues combined to produce a white screen in the packaged app:

1. **`package.json` "main" field mismatch:** TypeScript compiles `src/main.mts` → `dist/main.mjs` (due to `module: Node16` + `.mts` extension), but `package.json` pointed to `dist/main.js`. Electron loaded a stale `main.js` from a previous build, which was missing the `get-backend-url-sync` IPC handler and referenced the wrong preload file.

2. **`crossorigin` attribute on `file://` protocol:** Vite's production build added `crossorigin` to `<script>` and `<link>` tags. On Electron's `file://` protocol, this triggers CORS failures because there is no server to respond with headers, preventing the JavaScript bundle from loading.

**Fix:**
- `desktop/package.json`: Updated "main" from `dist/main.js` to `dist/main.mjs`
- `frontend/vite.config.ts`: Added `stripCrossOrigin()` Vite plugin to remove `crossorigin` attributes from built HTML

### Known Non-Blocking UX Items
- Phase 8 UX polish (loading states, error handling, validation, empty states, notifications, localization) — deferred to post-release
- Office Dashboard placeholder — KPI Dashboard already serves this role
- Custom application icon (using default Electron icon)

---

## Deferred Items

- **Office Dashboard** (`/office/dashboard`): DEFERRED. The KPI Dashboard at `/office/kpi` already provides real operational metrics and is the default post-login landing page. A separate office dashboard is not required for product completion.
