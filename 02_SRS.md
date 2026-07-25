Software Requirements Specification (SRS)
Sales, Inventory, Warehouse & Representative Management System (SIWRMS)
Document Version: 1.0
Date: 2026-07-23
Role: Senior Enterprise Software Architect
Status: Analysis & Architecture Only — No Implementation
1.	INTRODUCTION
1.1 Purpose
This SRS defines the functional and non-functional requirements for a professional enterprise system managing the full cycle of manufacturing, warehousing, sales, representative distribution, invoicing, and reporting for a manufacturing company, including two messenger bot integrations (Telegram + Bale).
1.2 Scope
The system covers product/catalog management, multi-warehouse inventory (factory + unlimited representative warehouses), inventory-event-sourced stock (no editable balances), two distinct sales models (local representative stock vs. factory-direct remote sales), customer & order lifecycle, invoicing, pricing, reporting, and bot-based lightweight access.
1.3 Intended Audience
Developers, DBAs, QA, DevOps, business stakeholders, and operations managers.
1.4 Document Conventions
Terminology is defined in §3 Glossary. All "MUST/MUST NOT/SHALL" statements are normative requirements.
2.	OVERALL DESCRIPTION
2.1 Product Perspective
A multi-tier enterprise application consisting of:
Backend API (core domain services)
Database (transaction-source-of-truth)
Web Frontend (admin/office users)
Representative portal (sales reps on web/mobile)
Two Messenger Bots (Telegram + Bale) for lightweight queries/order entry
Reporting/Batch layer
2.2 Operating Environment
Cloud or on-prem Linux server
Cross-platform web access (responsive)
Bot users on Telegram + Bale mobile clients
2.3 User Characteristics
Office staff (heavy web UI), warehouse operators (transactional UI), sales reps (portal + bots), management (reports/dashboard), customers (indirect via invoices).
2.4 Constraints & Compliance
Auditability: every inventory change must be reconstructable
Multi-currency consideration (at minimum local currency + optional FX)
Data retention / backup requirements
Bot API rate limits (Telegram + Bale)
2.5 Assumptions & Dependencies
Listed in §13.
3.	GLOSSARY / DEFINITIONS
Term Definition
Inventory Transaction Immutable ledger entry representing a single inventory movement
Factory Warehouse Single canonical origin warehouse manufacturing output enters
Representative Warehouse N quantity of satellite warehouses assigned to sales reps
Local Sale (Scenario A) Sale deducted from representative's own warehouse
Remote/Direct Sale (Scenario B) Sale shipped from factory; rep commission only; rep inventory untouched
Stock Transfer Movement of inventory between warehouses
Adjustment Manual correction transaction with mandatory reason/authorization
Lot Optional batch/production run identifier
4.	ACTORS
4.1 Human Actors
Actor Description Key Capabilities
A1 System Administrator Manages platform config, users, roles, integrations All system config, user mgmt, bot token mgmt, audit log access
A2 Office Manager / Sales Manager Oversees sales, orders, reps performance View all orders/reps, approve transfers, set prices, view reports, reassign reps
A3 Warehouse Operator (Factory) Manages factory warehouse inflow/outflow Receive production output, dispatch transfers, fulfill direct sales shipments
A4 Representative (Sales Rep) Sells products, manages own warehouse Receive stock from factory, sell to customers (Scenario A/B), view own inventory, place orders, view own commissions
A5 Accountant / Finance Manages invoices & payments Issue invoices, record payments, reconcile, manage pricing tiers
A6 Report Viewer / Management Strategic oversight Dashboards, reports (read-only)
A7 Bot User (Typically a representative) interacting via Telegram/Bale Query info, place orders, check own stock via bot
A8 Customer (external) Buys products Receives goods + invoices; not a direct system user (record only)
4.2 System / Non-Human Actors
Actor Description
S1 Inventory Ledger Engine Computes current stock from transaction streams
S2 Pricing Engine Resolves applicable price per product/customer/rep/tier
S3 Commission Engine Computes rep commissions (esp. Scenario B)
S4 Notification Service Event-driven notifications (internal + bots)
S5 Telegram Bot Adapter Telegram bot integration
S6 Bale Bot Adapter Bale messenger integration
S7 Reporting/Batch Service Scheduled + on-demand reporting
S8 Audit Service Immutable audit logging
5. ENTITIES (Conceptual Domain Model)
5.1 Core Catalog
E1 Product — SKU, name, description, unit of measure (UoM), category, dimensions/weight, status (active/discontinued)
E2 Category — hierarchical (parent/child), attributes
E3 Product Variant / SKU (optional) — size/color/grade variants
E4 Lot / Batch (optional) — production batch, manufacture/expiry dates
E5 Unit of Measure (UoM) — base + conversion (e.g. box = 12 units)
5.2 Warehousing
E6 Warehouse — type = FACTORY | REPRESENTATIVE, code, name, location/address, responsible user, status
E7 Warehouse Assignment — links rep ↔ representative warehouse (1)
E8 Bin/Location (optional) — sub-location within a warehouse
5.3 Inventory Ledger (Event-Sourced)
E9 InventoryTransaction — immutable; id, timestamp, product, lot, warehouse, quantity (signed), movement_type, reference (order/transfer/adjustment id), actor, reason, balance_after (cached), audit hash
E10 StockTransfer — source warehouse, destination warehouse, product(s), qty, status, operator, dispatch/receive timestamps
E11 StockAdjustment — warehouse, product, qty delta, reason code, authorized_by, evidence/reason text
5.4 Parties
E12 Representative — personal data, territory/region, assigned warehouse(s), commission config, status, contact, telegram/bale user id mapping
E13 Customer — name, type (individual/corporate), location/city, assigned rep, billing address, credit limit, status
E14 CustomerRepAssignment — which rep serves which customer
5.5 Sales / Orders
E15 Order — number, customer, rep, order_type (LOCAL | DIRECT/FACTORY_SHIP), lines, total, status, timestamps
E16 OrderLine — product, qty, unit price, discount, line total, fulfillment mode
E17 Fulfillment / Shipment — linked to order, source warehouse, tracking, status
E18 Commission — rep, order, amount, rate, status (accrued/paid), date
5.6 Invoicing & Finance
E19 Invoice — number, order(s), customer, lines, tax, total, status, issue date, due date
E20 InvoiceLine — product, qty, unit price, tax, line total
E21 Payment — invoice link, amount, method, date, reference
E22 Credit Note — returned/refunded items
5.7 Pricing
E23 PriceList — name, currency, validity period, customer tier/rep scope
E24 PriceListItem — product, unit price, min qty, discount tiers
E25 Discount — percentage/amount, scope (product/category/customer/rep)
5.8 System / Security / Integration
E26 User — auth account, role(s), linked rep/staff record
E27 Role — RBAC role, permissions
E28 Permission — granular action capability
E29 AuditLog — actor, action, entity, before/after, timestamp, ip
E30 BotSession — bot platform (TELEGRAM|BALE), user id mapping, linked rep, session state
E31 BotMessageLog — bot conversation logging
E32 Notification — type, recipient, payload, status
E33 Report — (generated/scheduled reports)
E34 ReportDefinition — parameters, schedule, owner, output format
6. BUSINESS RULES
6.1 Product & Catalog
BR-C1: A product MUST belong to at least one category.
BR-C2: Products can be marked discontinued; discontinued products cannot be ordered but historical transactions remain.
BR-C3: SKU is globally unique.
BR-C4: Optional lots/batches always recomputable to current stock.
6.2 Warehouse Rules
BR-W1: Exactly ONE warehouse is the canonical FACTORY warehouse (config flag); all others are REPRESENTATIVE.
BR-W2: REPRESENTATIVE warehouses are unlimited in number; each maps to one responsible representative.
BR-W3: A representative MAY be assigned one or more representative warehouses.
BR-W4: Stock transfers only valid between active warehouses.
BR-W5: A warehouse cannot be deactivated while it holds non-zero stock (or must be force-empty-justified + audited).
6.3 Sales Model Rules (Critical)
BR-S1 (Scenario A — Local Sale):
Order fulfillment source = representative's own warehouse. Inventory MUST decrement from that representative warehouse. Rep is the seller of record for fulfillment.
BR-S2 (Scenario B — Direct/Factory Sale):
When customer city ≠ representative's locality (remote sale): shipment originates from FACTORY warehouse directly to customer. The representative's inventory MUST NOT be affected. Rep receives a sales commission instead.
BR-S3: Order type (LOCAL vs DIRECT) is determined by: customer location vs rep locality, customer's assigned rep config, OR explicit override by authorized manager (with audit).
BR-S4: A direct/factory sale decreases FACTORY stock and accrues commission — it does NOT create a transfer to the rep.
BR-S5: A representative cannot sell from another representative's warehouse.
BR-S6: A local sale cannot complete if the rep warehouse lacks sufficient stock (per ledger projection) — order enters backorder or is rejected per policy.
BR-S7: Commission rate is configurable per rep / product / order_type and is calculated at order completion/invoice time.
6.4 Pricing Rules
BR-P1: Final price resolved by priority: customer-specific > rep-tier > product default.
BR-P2: Discounts apply within authorization limits.
BR-P3: Invoiced price is frozen at invoice issue time regardless of later price-list changes.
6.5 Invoicing & Finance
BR-F1: An invoice is generated from one or more (shipped/fulfilled) orders.
BR-F2: Payments are recorded against invoices; partial payments allowed.
BR-F3: A paid invoice cannot be edited; corrections use credit notes.
BR-F4: Credit notes generate reverse inventory transactions for returned goods.
6.6 Return / Refund Rules
BR-R1: Returns create negative inventory transactions into the appropriate source warehouse (factory for Scenario B, rep warehouse for Scenario A).
BR-R2: Returned/damaged goods recorded as separate lot/status (e.g. "damaged") — not commingled with sellable stock.
BR-R3: Commission clawback on returned Scenario-B sales.
6.7 Authorization / RBAC
BR-A1: Every state-changing action requires an authenticated actor + permission check.
BR-A2: Adjustments require an authorized approver (separation of duties) + mandatory reason.
BR-A3: All actions write to AuditLog.
6.8 Bot Rules
BR-B1: Bots operate scoped to the linked representative identity — a rep cannot query another rep's stock through the bot.
BR-B2: Bot order placement creates real Orders subject to the same business rules (LOCAL vs DIRECT).
BR-B3: Bot messages logged in BotMessageLog for audit.
7. INVENTORY RULES (Event-Sourced Ledger)
7.1 Core Principle
IR-1: Inventory balances are NEVER stored as directly editable numbers. They are materialized views computed from the immutable InventoryTransaction ledger.
IR-2: Current stock = SUM(quantity) of all transactions for (warehouse, product, [lot]) up to NOW.
IR-3: Every inventory movement MUST produce exactly one (or more) InventoryTransaction records within the same logical unit of work.
7.2 Transaction Immutability
IR-4: InventoryTransactions are append-only. NEVER UPDATE / DELETE. Corrections are new compensating transactions (reversal), never edits.
IR-5: Each transaction stores: timestamp, actor, movement_type, reference id, reason, signed quantity, and a sequential hash chained to the prior transaction (tamper-evidence).
7.3 Movement Types (enum)
RECEIPT_FROM_PRODUCTION, TRANSFER_IN, TRANSFER_OUT, SALE_OUT, SALE_RETURN_IN, ADJUSTMENT_POSITIVE, ADJUSTMENT_NEGATIVE, DAMAGED_OUT, FACTORY_DIRECT_SHIPMENT, INITIAL_OPENING_BALANCE, REVERSAL.
7.4 Double-Entry Consistency
IR-6: Transfers are TWO paired transactions: TRANSFER_OUT (source) + TRANSFER_IN (destination). For Scenario B direct shipment, only FACTORY_DIRECT_SHIPMENT (factory −) is posted — rep ledger untouched.
IR-7: Stock cannot go negative: a transaction that would drive a warehouse-product balance below zero is REJECTED at validation time (concurrency-safe).
IR-8: Negative deltas allowed only where they don't breach zero net stock.
7.5 Concurrency
IR-9: Inventory reservations use optimistic concurrency / per (warehouse, product) locks or serializable isolation to prevent overselling.
IR-10: Optional reservation phase (holds) before commit to prevent race conditions on concurrent local sales.
7.6 Reconciliation
IR-11: Physical count exports created transactions of type INITIAL_OPENING_BALANCE or ADJUSTMENT_* with reason "stocktake".
IR-12: Materialized balance snapshots are cache-only; ledger is the source of truth. Snapshots are validated/refreshed by a scheduled reconciliation job.
7.7 Mapping Movement → Transaction
Event Transaction(s)
Production received into factory RECEIPT_FROM_PRODUCTION (+ factory)
Transfer factory→rep TRANSFER_OUT (− factory) + TRANSFER_IN (+ rep)
Scenario A sale SALE_OUT (− rep)
Scenario B sale FACTORY_DIRECT_SHIPMENT (− factory); NO rep tx
Customer return (Scenario A) SALE_RETURN_IN (+ rep)
Customer return (Scenario B) reversal SALE_RETURN_IN (+ factory)
Damage DAMAGED_OUT (− rep or factory)
Manual correction ADJUSTMENT_POSITIVE/NEGATIVE (+/−) + authorized reason
Stockcount delta ADJUSTMENT_* "stocktake"
8. ORDER WORKFLOW
8.1 Order Lifecycle (states)
DRAFT → PENDING_APPROVAL → APPROVED → RESERVED → FULFILLING → SHIPPED → INVOICED → PAID → COMPLETED
Branches/terminals: CANCELLED, BACKORDERED, PARTIALLY_FULFILLED, RETURNED.
8.2 Workflow Steps
Creation — Order created by rep (web/bot) or office. Order lines reference product + qty.
Type determination — System proposes LOCAL or DIRECT per BR-S3. Manager may override (audit-logged).
Pricing — Pricing Engine resolves unit prices (BR-P1). Discounts validated.
Validation / Reservation —
LOCAL: check rep-warehouse stock ≥ qty (ledger projection). If insufficient → BACKORDERED or rejected.
DIRECT: verify factory stock ≥ qty. Rep inventory untouched.
Reserve stock (IR-10) → status RESERVED.
Approval — per policy (auto-approve under thresholds; manager approval above).
Fulfillment —
LOCAL: pick/pack from rep warehouse → SALE_OUT transaction → SHIPPED.
DIRECT: factory dispatches → FACTORY_DIRECT_SHIPMENT transaction → SHIPPED → generate Shipment record.
Commission accrual — Commission Engine accrues rep commission (esp. Scenario B). Scenario A may or may not carry commission per policy.
Invoicing — Invoice generated from shipped order → INVOICED.
Payment — Payments recorded → PAID → COMPLETED (when fully paid + fulfilled).
Returns — if any, reverse transactions + commission clawback + credit note.
8.3 State Transitions (guarded)
DRAFT → PENDING_APPROVAL: submit
PENDING_APPROVAL → APPROVED / REJECTED
APPROVED → RESERVED: stock reserved
RESERVED → FULFILLING
FULFILLING → SHIPPED
SHIPPED → INVOICED
INVOICED → PAID → COMPLETED
Any pre-SHIPPED → CANCELLED (releases reservation)
Post-shipment → RETURNED (reverse tx)
9. WAREHOUSE WORKFLOW
9.1 Factory Warehouse Inbound
Production output received → RECEIPT_FROM_PRODUCTION (+ factory) with optional lot → available stock increases.
9.2 Stock Transfer (Factory → Representative)
Office/rep creates StockTransfer request (source=factory, dest=rep warehouse, lines).
Manager approval (per policy/threshold).
Dispatch at factory → TRANSFER_OUT (− factory) + transfer status DISPATCHED.
Receive at rep warehouse → TRANSFER_IN (+ rep) + transfer status RECEIVED.
Discrepancy handling → ADJUSTMENT at destination with reason (loss in transit).
9.3 Representative → Representative Transfer (optional, configurable)
Allowed only if policy enabled; same double-entry pair model.
9.4 Factory Direct Shipment (Scenario B)
Triggered by DIRECT order → FACTORY_DIRECT_SHIPMENT (− factory) only.
Rep warehouse ledger NOT touched (this is the key distinction from transfers and Scenario A).
9.5 Stocktake / Reconciliation
Periodic physical count → deltas become ADJUSTMENT_* "stocktake" → audit + reason required.
Reconciliation job verifies cached snapshots vs ledger.
9.6 Damage / Write-off
DAMAGED_OUT transaction with reason; tracked under damaged lot/status; prevents resale.
10. REPRESENTATIVE WORKFLOW
10.1 Onboarding
Admin creates Representative record (territory, contact, commission config).
Admin creates/enables REPRESENTATIVE warehouse(s) and assigns to rep.
Rep User account created + role + bot identity linking (Telegram/Bale user id).
Initial stock: via StockTransfer from factory (or opening balance if migrating).
10.2 Day-to-Day Operations
View own warehouse stock (ledger-derived).
Receive transfers from factory (confirm receipt).
Create orders:
Local customer (same locality) → Scenario A (deduct own stock).
Remote customer (other city) → Scenario B (factory ships, commission only, no stock change).
View own commission accruals and order history.
Lightweight access via Telegram/Bale bots.
10.3 Commission Handling
Scenario B → commission accrued per rate (BR-S7).
Scenario A → commission per policy (optional).
Commission status: ACCRUED → APPROVED → PAID.
Clawback on returns/refunds.
10.4 Reporting (rep view)
Own sales, own stock, own commissions, own order history, own warehouse transactions.
10.5 Offboarding / Reassignment
Rep deactivated: cannot create orders; stock must be transferred back to factory or reassigned.
Customer-rep reassignment handled via CustomerRepAssignment with effective date; audit-logged.
11. EDGE CASES
Edge Case Handling
EC1 Concurrent local sales exceed rep stock Reservation/optimistic lock prevents oversell; losing order backordered
EC2 Transfer lost in transit TRANSFER_OUT posted; TRANSFER_IN not posted; ADJUSTMENT_NEGATIVE at source OR ADJUSTMENT at dest for variance; audit + reason
EC3 Order changed from DIRECT to LOCAL post-approval Requires reversal of factory shipment transaction + new rep SALE_OUT; manager override + audit
EC4 Customer locality ambiguous Explicit order_type selection required; system flags and asks for manager confirmation
EC5 Damaged/returned goods commingled Enforce lot/status segregation; saleable balance excludes damaged sub-ledger
EC6 Rep warehouse has zero stock but tries local sale Rejected/BACKORDERED; suggest conversion to DIRECT order
EC7 Discontinued product in open order Fulfill existing reserved; block new orders
EC8 Ledger cache vs actual mismatch Reconciliation job repairs cache from ledger; flags discrepancy for audit
EC9 Bot user identity spoofing/mis-link Strong identity binding; refresh tokens; restrict to own scope
EC10 Partial payment on invoiced order Allowed; invoice stays PARTIALLY_PAID until full
EC11 Return after commission paid Clawback creates negative commission entry (status reversed/recovered)
EC12 Factory stock insufficient for Scenario B Order backordered at factory or rejected; rep commission not accrued until shipped
EC13 Time-zone / timestamp conflicts across cities All timestamps UTC; locality is a separate location attribute (not timezone-derived)
EC14 Two reps claim same customer Resolved by CustomerRepAssignment effective-date precedence + manager arbitration
EC15 Transfer partially received Per-line partial receipt; remaining lines stay DISPATCHED
EC16 Manual adjustment abuse Mandatory approver separation + reason + audit + threshold limits
EC17 Lot expiry (if lots enabled) Expiry-based allocation (FEFO); block expired stock from sale
EC18 Bot API outage (Telegram/Bale down) Bot failures queued/retried; never affect order DB consistency; ledger integrity untouched
12. ASSUMPTIONS
The company uses a single local currency by default; FX support is a future option.
One factory warehouse is canonical; supported systems assume single factory.
Each representative operates primarily within a defined territory/locality.
Customer city metadata is reliable enough to drive Scenario A vs B by default (with override).
Telegram and Bale bot platforms expose stable APIs and long-lived bot tokens.
Network connectivity is generally available; offline-first is out of scope for v1.
Concurrent order volume is within RDBMS transactional limits (no extreme high-throughput IoT-scale).
Tax rules are manageable per-region with manual configuration in v1.
Single-tenant deployment for v1 (SaaS multi-tenancy is a future expansion).
Existing legacy data (if any) is imported via initial opening-balance transactions during migration.
13. FUTURE EXPANSION POSSIBILITIES
Multi-tenancy / SaaS — multiple manufacturing companies on one deployment.
Multi-currency & FX — currency conversion, multi-currency invoicing.
Multi-factory support — more than one factory warehouse with routing logic for Scenario B.
Advanced demand forecasting & restock recommendations (ML).
Barcode/QR & RFID warehouse operations — scanning at every transaction for full traceability.
Mobile-native apps (offline-capable) for reps and warehouse operators.
Customer self-service portal — view invoices, order history, place orders.
Additional messenger bots (WhatsApp Business, Signal, integrated webchat).
AI assistant via bots — natural-language queries for reps ("what's my stock of X?").
Route optimization & shipment tracking integration (courier APIs) for Scenario B.
Landed cost / cost accounting — actual cost-of-goods per transaction for margin reporting.
Lot/batch full traceability + expiry (FEFO/FEFIFO) recall management.
Approval workflow engine — configurable multi-step approvals (BPMN-like).
Webhooks / ERP integration (accounting ERP sync for invoices/payments).
Advanced BI dashboards (Power BI / Metabase / Superset).
Multi-language UI (i18n).
Event streaming platform (Kafka) for real-time inventory analytics at scale.
Blockchain-style ledger anchoring for tamper-evident external audit.
14. RECOMMENDED TECHNOLOGY STACK
14.1 Architecture Style
Modular Monolith (domain modules: Catalog, Inventory, Sales, Finance, Representatives, Bots, Reporting) with clear bounded contexts — evolvable to microservices later via the Inventory Ledger as the natural seam.
14.2 Stack Recommendation
Layer Recommendation Rationale
Primary Backend Language/Platform Java 21 + Spring Boot 3 (or .NET 8 / ASP.NET Core) Enterprise-grade, strong transactions, mature ecosystem, RBAC, long-term support
Alternative (leaner team) Node.js + NestJS (TypeScript) or Python + Django/FastAPI Faster start if team expertise exists
Database PostgreSQL 16 ACID, strong constraints, window functions for ledger balance computation, JSONB for flexible metadata, row-level security for rep scoping
ORM / Data Access JPA/Hibernate or Entity Framework (or Prisma/TypeORM on Node) with explicit transaction control Ledger immutability enforced at DB layer + app layer
Inventory Ledger Integrity DB triggers/sequences + a hash-chain column (SHA-256 of prior hash + payload); UPDATE/DELETE restricted via grants Tamper-evident append-only ledger
Migration Flyway / Liquibase / Alembic Versioned schema evolution
Caching / Materialized balances Redis (snapshot cache), refreshed from ledger; never authoritative Faster stock reads
Web Frontend React + TypeScript + Vite + TailwindCSS, state via TanStack Query; admin UI via shadcn/ui Modern, maintainable
Representative Portal Same React app, role-routed (responsive/PWA) Single codebase
Authentication / RBAC OAuth2/OIDC (Keycloak or Auth0) + JWT; role/permission tables Enterprise auth, SSO-ready
Telegram Bot node-telegram-bot-api (Node) or TelegramBots (Java/.NET); long-polling or webhook Mature libraries
Bale Bot Bale messenger official SDK / HTTP API adapter Native integration
Bot Framework (shared) Adapter pattern — common command router → platform-specific adapters (Telegram/Bale) Extensible to more bots
Async / Background jobs e.g., Spring Scheduler / Quartz / BullMQ / Celery Reports, reconciliation, retries, commission settlement
Messaging / Events RabbitMQ (modular monolith) → Kafka (future scale) Decouple bots, notifications, reporting
Notifications Internal + bot push via event bus Idempotent delivery
Reporting On-demand SQL + scheduled jobs export to PDF/Excel; BI via Metabase/Superset (optional) Cost-effective, extensible
File storage S3-compatible (MinIO on-prem / AWS S3) Attachments, report outputs
Containerization / Deploy Docker + Docker Compose (dev) → Kubernetes (prod ready) Portable, scalable
CI/CD GitHub Actions / GitLab CI Automated test + deploy
Monitoring / Observability Prometheus + Grafana + Loki logs + OpenTelemetry tracing Enterprise-grade ops
Secrets / Config Vault / AWS Secrets Manager / env injection Bot tokens, DB creds
API style REST (primary) + OpenAPI spec; optional GraphQL for frontend flexibility later Standard, documented
Testing JUnit/pytest/Jest unit + integration; Playwright E2E Ledger correctness is critical—transactional integration tests required
14.3 Critical Data-Integrity Mechanisms
Append-only enforcement on inventory_transaction (revoke UPDATE/DELETE from app role; only INSERT).
Hash-chain column for tamper-evidence + periodic integrity verification job.
Optimistic concurrency / SELECT ... FOR UPDATE or advisory locks on (warehouse_id, product_id) during stock mutations.
DB-level CHECK that quantity sign matches movement_type.
All stock reads resolved from ledger; cached snapshots marked non-authoritative.
15. PROJECT COMPLEXITY ESTIMATE
15.1 Complexity Rating
Medium-High (≈ 7/10)
15.2 Complexity Drivers
Driver Level Reason
Inventory ledger (event-sourced, immutable, hash-chain) High Append-only, concurrency, double-entry pairs, reconciliation
Dual sales model (Scenario A vs B) High Divergent fulfillment + commission + stock logic; easy to get wrong
Multi-warehouse + unlimited reps Medium-High Scale + scoping + RBAC
Order state machine Medium Many states + branches + overrides
Invoicing / payments / returns / commission clawback Medium-High Financial correctness, no edits post-issue
Two bot integrations (Telegram + Bale) Medium Adapter pattern reduces risk; identity binding is the trap
RBAC + audit Medium Foundational, but pervasive
Reporting Medium Many reports; ledger-based aggregation
15.3 Size Estimate (rough order of magnitude)
Domain entities: ~34 (see §5) → ~50–70 DB tables (incl. join/history/audit)
API endpoints: ~120–180
Backend modules: 7 bounded contexts
Estimated effort (mid-size team, 4–6 engineers): ~5–8 months to production v1; risk areas (ledger + Scenario B + bots) buffer to ~9 months realistically.
Estimated effort (solo/small team): ~9–14 months.
15.4 Key Risk Areas
Inventory ledger correctness (highest business-critical risk — any bug here corrupts financial reporting).
Scenario A vs B branching (subtle; misrouting deliveries causes wrong stock deductions and wrong commissions).
Concurrency on concurrent sales/low-stock.
Bot identity↔representative binding (security + data-scope leak risk).
Return/commission-clawback correctness.
Reconciliation between cached balances and the ledger.
15.5 Mitigations
Build the Inventory Ledger module first with exhaustive transactional integration tests before any sales logic.
Encode Scenario A/B routing as explicit, testable, single-responsibility service + golden-path tests.
Force audit + approval on ALL adjustments; deny ad-hoc stock edits entirely.
Keep bot adapters thin; identity resolution centralized and scoped.
Continuous reconciliation job + alerting on snapshot/ledger mismatch.
16. CLOSING
This document covers the full analysis & architecture for the SIWRMS:
Complete SRS ✓
All Actors (human + system) ✓
All Entities ✓
Business rules ✓
Inventory rules (event-sourced, immutable, no editable balances) ✓
Order workflow ✓
Warehouse workflow ✓
Representative workflow ✓
Edge cases ✓
Assumptions ✓
Future expansions ✓
Technology stack ✓
Complexity estimate ✓
As instructed, no source code, no folders, no files, and no implementation have been produced. This is strictly system analysis and architecture, ready to drive the implementation phase whenever you choose to proceed.
I'll stop here per instructions. Ready to refine any section (e.g., drill deeper into the Inventory Ledger schema design, order state machine, or bot adapter architecture) whenever you'd like to continue.

