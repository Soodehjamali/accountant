# Implementation Readiness Audit — SIWRMS (`accountant` repository)

**Audited repository:** https://github.com/Soodehjamali/accountant
**Audit date:** 2026-08-18
**Scope:** Read-only analysis of all 14 listed documents. No code, folders, or specifications were created or modified. This document reports findings only.

---

## 1. Current Repository State

The repository root contains **no application code**. It is a documentation-only repository at commit history depth of 12 commits.

| Path | Type | Notes |
|---|---|---|
| `.claude/` | folder | Claude Code configuration; contents not enumerable via available tooling (GitHub blocked automated directory browsing) |
| `database/` | folder | Contents not enumerable via available tooling — **must be manually verified by the repo owner before Task 2 begins**, in case it already holds partial work |
| `venv/` | folder | A Python virtual environment appears to be committed to git. This is not evidence of a ratified stack decision — it may be an accidental commit — and should not be treated as an implicit "Python has been chosen" signal. Flagged as a risk (§9) and a housekeeping item (should normally be `.gitignore`d) |
| `01_Project_Vision.md` – `10_Development_Roadmap.md`, `BusinessInvariants.md`, `CLAUDE.md`, `DomainEvents.md`, `Domains.md` | files | Documentation set — see §3/§4 below for per-file completeness |
| `_fkdump.txt` | file | A raw FK-relationship dump, evidently a working artifact produced while authoring `06_ERD.md` (it lists `len=N | 'FK text' || table name` rows matching ERD entries). It is **not DDL, not a migration, and not application code** |

There is no `README.md`, `requirements.txt`, `package.json`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.gitignore`, CI config, or any entry-point file (`main.py`, `app.py`, `manage.py`, `server.js`, etc.) anywhere in the repository.

**Conclusion: the repository is 100% pre-implementation.** This matches the prompt's premise exactly and is confirmed by the project's own roadmap (§3).

---

## 2. Target Architecture (as documented)

### 2.1 Folder structure (from `08_Architecture.md`)

```
project-root/
├── apps/
├── backend/
├── frontend/
├── docs/
│   ├── 01_Project_Vision.md
│   ├── 02_SRS.md
│   ├── 03_Business_Rules.md
│   ├── 04_Business_Policies.md
│   ├── 05_Domain_Model.md
│   ├── 06_ERD.md
│   ├── 07_API_Contracts.md
│   ├── 08_Architecture.md
│   ├── 09_Decisions.md
│   ├── 10_Development_Roadmap.md
│   └── changelog.md
└── README.md
```

**Conflict noted:** `08_Architecture.md` names `07_API_Contracts.md` as a docs file, but the actual repository has `07_DATABASE_SPEC.md` at that position instead, and no `07_API_Contracts.md` exists anywhere. This is either a stale architecture doc or a signal that an API-contracts document is still owed. See §6.

### 2.2 System shape (from `02_SRS.md` §2.1)

A multi-tier system: Backend API (core domain services) + Database (source of truth) + Web Frontend (admin/office) + Representative portal + Two messenger bots (Telegram, Bale) + Reporting/Batch layer.

### 2.3 Domain/module boundaries

`02_SRS.md` §14.1 names **7 bounded contexts**: Catalog, Inventory, Sales, Finance, Representatives, Bots, Reporting — packaged as a **Modular Monolith**, evolvable to microservices later via the Inventory Ledger as the natural seam.

`Domains.md`, by contrast, names only **3** domains: Sales, Inventory, Customer. See §6 for this conflict.

---

## 3. Existing Components

Nothing executable exists. What exists is documentation, at varying levels of completeness:

| Document | Lines/Size | Completeness |
|---|---|---|
| `01_Project_Vision.md` | 17 lines / 253 B | Complete — short goal statement |
| `02_SRS.md` | 393 lines / 27.8 KB | Complete, explicitly closed ("Status: ... ready to drive the implementation phase") |
| `03_Business_Rules.md` (BRF) | 11 lines / 717 B | **Appears truncated** — cuts off mid-sentence at "BRF-PP1: Each receipt of inventory by a representative MAY occur at a different purchase price depending on the..." No closing/footer content follows, unlike every other file. Needs owner verification — this may be a genuinely incomplete file rather than a fetch artifact |
| `04_Business_Policies.md` | 28 lines / 569 B | Ends with a literal `...` — signals the author considers it incomplete/a stub |
| `05_Domain_Model.md` | **0 lines / 0 bytes** | **Completely empty.** Listed as an authoritative source in this audit's instructions but contains nothing |
| `06_ERD.md` | 231 lines / 58.9 KB | Extremely thorough — 78 logical tables, enums, aggregates, indexes, partitioning, scalability notes. Self-declared "ERD FULLY COMPLETE" |
| `07_DATABASE_SPEC.md` | 1240 lines / 95.1 KB | Extremely thorough per-table physical spec (columns, constraints, indexes) for most/all ERD tables |
| `08_Architecture.md` | 19 lines / 487 B | Minimal — folder skeleton only, no tech names |
| `09_Decisions.md` | 41 lines / 390 B | 3 ADRs, all **domain-rule** decisions (inventory ledger, Customer aggregate root, no negative stock). **No technology-stack ADR exists** |
| `10_Development_Roadmap.md` | 37 lines / 199 B | 5-task checklist, see §3.1 |
| `BusinessInvariants.md` | 26 lines / 241 B | 3 short invariants, approved status |
| `CLAUDE.md` | 19 lines / 385 B | AI development rules (DDD, no code before design approval, ADR required for breaking changes) |
| `DomainEvents.md` | 21 lines / 195 B | 10 named events, no payloads/schemas |
| `Domains.md` | 26 lines / 236 B | 3 domains only (see §6) |

### 3.1 Development Roadmap status (authoritative — `10_Development_Roadmap.md`)

| Task | Status |
|---|---|
| Task 0 — Repository | ✅ Done |
| Task 1 — Architecture | ✅ Done |
| Task 2 — Database | ⬜ Not started |
| Task 3 — API | ⬜ Not started |
| Task 4 — Backend | ⬜ Not started |

**Important:** this roadmap names only 5 tasks total, and **frontend and bots are not tracked as roadmap tasks at all**, despite being explicitly in-scope per `02_SRS.md`. This is a scope gap between the roadmap and the SRS (§6).

---

## 4. Missing Components

Everything downstream of documentation is missing:

- Physical database (no migrations, no DDL, no seed data) — despite `07_DATABASE_SPEC.md` being detailed enough to generate this from
- Backend service code (any language)
- API layer / OpenAPI contract (`08_Architecture.md` references `07_API_Contracts.md` — this file does not exist)
- Frontend (admin/office web UI, representative portal)
- Telegram bot adapter
- Bale bot adapter
- Auth/RBAC implementation (roles/permissions are modeled in the ERD as tables only)
- Reporting/batch layer
- CI/CD configuration
- Containerization (Dockerfile / docker-compose)
- Tests of any kind
- A ratified technology-stack decision (see §5 — this is a **decision gap**, not just a code gap)

---

## 5. Technology Decisions Supported by the Documents

Per your instruction, nothing below is assumed beyond what the documents say — and where a document only "recommends" rather than decides, that distinction is preserved.

| Layer | What the documents say | Status |
|---|---|---|
| **Database engine** | `06_ERD.md` and `07_DATABASE_SPEC.md` are written in Postgres-specific syntax throughout: `UUID`, `JSONB`, `TIMESTAMPTZ`, `gen_random_uuid()`, partial indexes, range partitioning, `BEFORE INSERT/UPDATE` triggers, column-level `GRANT`/`REVOKE`. | **De facto locked in by two authoritative, "complete" documents**, even though `02_SRS.md` §14 frames it as a "Recommendation." This is the one layer with strong, consistent cross-document support |
| **Backend language/framework** | `02_SRS.md` §14.2 lists **four alternatives** as a recommendation table: (1) Java 21 + Spring Boot 3 or .NET 8/ASP.NET Core, (2) Node.js + NestJS (TypeScript), (3) Python + Django/FastAPI. No ADR in `09_Decisions.md` selects one. `08_Architecture.md` names only a generic `backend/` folder. | **Not decided.** Do not assume FastAPI, Django, Spring, NestJS, or any other framework |
| **Frontend framework** | `02_SRS.md` §14.2 recommends React + TypeScript + Vite + TailwindCSS, shadcn/ui, TanStack Query — but again as a recommendation row in a table, not an ADR | **Not decided.** Do not assume React, Next.js, Vue, or any other framework |
| **ORM/migrations** | Recommended: JPA/Hibernate or Entity Framework or Prisma/TypeORM; Flyway/Liquibase/Alembic for migrations | **Not decided** — tied to the undecided backend language |
| **Auth** | Recommended: OAuth2/OIDC via Keycloak or Auth0 + JWT | **Not decided** |
| **Messaging/queue** | Recommended: RabbitMQ (monolith stage) → Kafka (future scale) | **Not decided** |
| **File storage** | Recommended: S3-compatible (MinIO on-prem / AWS S3) — referenced again in `06_ERD.md`'s attachment/generated_document tables and scalability notes | Consistently referenced across 2 documents, but still phrased as recommendation, not ADR |
| **Architecture style** | Modular Monolith with 7 bounded contexts, evolving to microservices later via the Inventory Ledger as the seam | Clearly and consistently stated — this is the closest thing to an architectural ADR that exists, though it is still inside the SRS "recommendation" section rather than `09_Decisions.md` |
| **Containerization** | Docker + Docker Compose (dev) → Kubernetes (prod-ready) | Recommendation only |

**Bottom line:** Only the **database engine (PostgreSQL)** and the **modular-monolith / 7-bounded-context architecture style** have strong enough, consistent enough documentary support to treat as effectively decided. Every specific backend/frontend framework remains genuinely open per the documents' own "recommendation, not decision" framing, and per `09_Decisions.md` containing no stack-selection ADR at all. Per `CLAUDE.md`'s own rule ("No breaking changes without ADR" / "Never generate code before design approval"), **a tech-stack ADR is a prerequisite that has not yet been produced.**

---

## 6. Document Conflicts

1. **`05_Domain_Model.md` is empty (0 bytes)** but is listed as an authoritative source in this audit's own instructions. In practice, `06_ERD.md` §0.1 ("Domain-Model Corrections Applied") and Part J ("Aggregate Mapping") appear to carry the domain-model content that this file was presumably meant to hold. This should be either populated or formally superseded by a note in `09_Decisions.md`.

2. **Stock transfer confirmation — direct contradiction.**
   - `04_Business_Policies.md`: *"Transfer immediately changes warehouse inventory. Representative confirmation is not required."*
   - `02_SRS.md` §9.2 and `06_ERD.md` (table `stock_transfer`, states `DISPATCHED`/`IN_TRANSIT`/`RECEIVED`/`PARTIAL_RECEIVED`) and `07_DATABASE_SPEC.md` (T4/T5): describe a **two-phase, double-entry transfer** — `TRANSFER_OUT` posted at dispatch, `TRANSFER_IN` posted only at receipt, with partial-receipt support and discrepancy/variance handling.
   These cannot both be true as written. This must be resolved before any transfer/inventory logic is built, since it changes the ledger-posting model.

3. **Invoice immutability — blanket rule vs. staged rule.**
   - `BusinessInvariants.md` INV-003: *"Invoices are immutable."* (unqualified)
   - `06_ERD.md` (table `invoice`) and `07_DATABASE_SPEC.md` (T17): invoices are mutable while `DRAFT`, and only become immutable once `state IN ('ISSUED', 'PAID', 'CLOSED_CORRECTED')`, enforced by a `BEFORE UPDATE` trigger checking state.
   INV-003 as literally written would forbid editing a draft invoice at all, which conflicts with the documented invoice lifecycle. Likely just under-specified wording in the invariant, but should be tightened before it's used as a DB-constraint spec.

4. **Domain/bounded-context scope mismatch.**
   - `Domains.md` names 3 domains: Sales, Inventory, Customer.
   - `02_SRS.md` §14.1 names 7 bounded contexts: Catalog, Inventory, Sales, Finance, Representatives, Bots, Reporting.
   - `06_ERD.md`/`07_DATABASE_SPEC.md` additionally cover RBAC/Audit, Commission, Notifications/Bot messaging, Returns, Files/Documents — none of which appear in `Domains.md` at all.
   `Domains.md` looks like an early/incomplete draft relative to the other two documents.

5. **Architecture doc references a file that doesn't exist.** `08_Architecture.md`'s target `docs/` listing includes `07_API_Contracts.md`; the actual repository has `07_DATABASE_SPEC.md` in that slot and no API-contracts document anywhere. Either `08_Architecture.md` is stale, or an API-contracts document is an outstanding deliverable (this would need to exist before or during Task 3 — API).

6. **Representative pricing authority — potential rule interaction, not a hard contradiction, but unresolved.**
   - `04_Business_Policies.md`: *"Representative may change selling price. Price change affects only current invoice."*
   - `02_SRS.md` BR-P1 (price resolved by priority: customer-specific > rep-tier > product default) and BR-P2 (discounts apply "within authorization limits").
   It's not written anywhere how a rep's ad-hoc price override interacts with the Pricing Engine's precedence chain, `order_price_freeze` (H6/T13 in the ERD), or authorization limits. This is a genuine gap, not just a wording issue, and should be clarified before the Pricing/Order domain is built.

7. **`03_Business_Rules.md` (BRF) appears cut off** after its first rule (BRF-PP1), with no closing content, unlike every other document in the set. Should be confirmed with the repository owner whether this file is genuinely incomplete or whether content exists that wasn't retrievable through available tooling.

8. **ERD table count vs. database spec — not fully cross-verified in this audit.** `06_ERD.md` states a final count of 78 logical tables (Part G.5 + the returns/carrier/notification-history/reporting-snapshot addendum). `07_DATABASE_SPEC.md` was read in full and does appear to cover a matching set of tables (T1–T28, H1–H10, M13–M17, J1–J2, R14, plus earlier reference/master/config sections), but an exhaustive line-by-line reconciliation of all 78 tables between the two documents was not performed as part of this audit and should be done as a pre-DDL verification step.

---

## 7. Implementation Dependencies

```
Tech-stack ADR (backend language, ORM, migration tool)
        │  [blocks everything below — governance gate per CLAUDE.md]
        ▼
Documentation reconciliation (§6 conflicts resolved,
05_Domain_Model.md gap closed or formally waived)
        │
        ▼
Task 2 — Physical Database
  ├── Reference/lookup tables (Part B) ─────┐
  ├── Master data tables (Part C) ──────────┤ (must exist before ledger FKs)
  └── Inventory Ledger (inventory_transaction,
      hash-chain, append-only constraints)   ← SRS §15.5: build + test this
                                                 FIRST, before any sales logic
        │
        ▼
Task 3 — API layer (needs finalized schema; module boundaries
          should follow whichever domain list is ratified — §6.4)
        │
        ├──────────────┬───────────────────┬─────────────────┐
        ▼              ▼                   ▼                 ▼
Task 4 — Sales/Order   Finance/Invoicing   Commission        RBAC/Audit
  (Scenario A/B         + Customer Ledger   engine            (needed by
   routing — SRS's       (needs Order        (needs Order      almost every
   2nd-highest risk       domain)             domain)          other module)
   area)
        │              │                   │
        └──────┬───────┴───────────────────┘
               ▼
          Reporting (needs populated ledgers to be meaningful)
               │
        ┌──────┴──────┐
        ▼             ▼
   Bots (Telegram/   Frontend (web admin + rep portal)
   Bale) — needs
   Backend + API +
   RBAC first
```

Key dependency notes:
- The **Inventory Ledger** is called out by `02_SRS.md` §15.4–15.5 itself as the highest business-critical risk area and the explicit recommendation is to build and exhaustively test it *before* any sales logic — this should govern milestone ordering, not just table-creation order.
- **Bots depend on backend + API + RBAC/identity binding**, not the other way around — `02_SRS.md` EC9 flags bot-identity spoofing as a named edge case requiring "strong identity binding" before bots go live.
- **Frontend has no roadmap task at all** (§3.1) — it should be explicitly scoped/added to the roadmap before work begins, or explicitly deferred, so it isn't silently forgotten.
- **Reporting** is documented as reading from the three event-sourced ledgers (inventory, customer ledger, commission) and from `kpi_snapshot`/`report_snapshot` — it has no meaningful data to report on until Sales + Finance + Commission are live.

---

## 8. Recommended Milestone Sequence

This sequence follows the existing `10_Development_Roadmap.md` task list (Task 0/1 done, Task 2 next) and layers in the dependency graph from §7 and the risk priorities from `02_SRS.md` §15.

| # | Milestone | Corresponds to roadmap task | Notes |
|---|---|---|---|
| M0 | **Documentation reconciliation + Technology-Stack ADR** | (governance, precedes Task 2) | Resolve §6 conflicts; add an ADR to `09_Decisions.md` selecting backend language/framework, ORM, migration tool, and (if in scope for v1) frontend framework. Required by `CLAUDE.md`'s own rules before any code is written |
| M1 | **Task 2 — Database: reference + master schema** | Task 2 | Enums/lookup tables (Part B) and master data tables (Part C) per `07_DATABASE_SPEC.md`, in PostgreSQL |
| M2 | **Task 2 — Database: Inventory Ledger** | Task 2 (continued) | `inventory_transaction` with hash-chain, append-only grants, negative-stock guard trigger, plus `inventory_balance_snapshot` reconciliation job design. Treated as its own milestone per SRS's explicit risk-mitigation instruction |
| M3 | **Task 2 — Database: remaining transactional/financial/audit schema** | Task 2 (continued) | Orders, shipments, invoices, payments, customer ledger, commission ledger, RBAC, audit log, notifications, reporting tables |
| M4 | **Task 3 — API contracts + Catalog/Warehouse/Inventory endpoints** | Task 3 | Produces the missing `07_API_Contracts.md` (or equivalent) referenced by `08_Architecture.md` |
| M5 | **Task 4 — Backend: Sales/Order domain (Scenario A vs B)** | Task 4 | SRS's 2nd-highest risk area; build as an isolated, heavily tested service per §15.5 |
| M6 | **Task 4 — Backend: Finance (Invoicing, Payments, Customer Ledger, Credit Notes)** | Task 4 (continued) | |
| M7 | **Task 4 — Backend: Commission engine** | Task 4 (continued) | |
| M8 | **Task 4 — Backend: RBAC/Auth + Audit log** | Task 4 (continued) | Should land no later than here since almost every other module depends on actor/permission checks |
| M9 | **Reporting/Batch layer** | not yet in roadmap | Needs M5–M8 populated to be meaningful |
| M10 | **Bots (Telegram, Bale)** | not yet in roadmap | Thin adapters per SRS; identity-binding security work is the critical path item |
| M11 | **Frontend (admin/office web, representative portal)** | not yet in roadmap | Currently unscoped in the roadmap — recommend adding explicitly rather than leaving implicit |

---

## 9. Risks

| Risk | Source | Severity |
|---|---|---|
| No ratified technology-stack ADR exists; building without one violates the project's own `CLAUDE.md` rule and risks a costly framework swap later | §5, `09_Decisions.md` gap | High |
| `05_Domain_Model.md` is empty despite being an authoritative source; domain-model corrections currently live only inside `06_ERD.md` §0.1/Part J | §6.1 | Medium |
| Stock-transfer confirmation rule directly contradicts across two documents (§6.2) — building the wrong model corrupts inventory semantics from day one | §6.2 | High |
| Invoice immutability invariant is under-specified relative to the documented invoice state machine (§6.3) — risk of over- or under-constraining the DB | §6.3 | Medium |
| `Domains.md` under-scopes the bounded contexts relative to SRS/ERD — if used to define API/service module boundaries, several domains (Finance, Commission, RBAC, Reporting, Bots) would be missing | §6.4 | Medium |
| Ledger correctness (inventory, customer, commission) is explicitly named by the SRS itself as the single highest business risk in the entire system | `02_SRS.md` §15.4 | High (acknowledged by source docs) |
| Scenario A vs. B (local vs. factory-direct) misrouting risk is explicitly flagged by the SRS as easy to get wrong and financially consequential | `02_SRS.md` §15.4 | High (acknowledged by source docs) |
| Bot identity↔representative binding is a named security/data-scope-leak risk (EC9) | `02_SRS.md` | Medium-High |
| `venv/` is committed to git; contents/purpose unverified — may be accidental, and should not be read as an implicit Python decision | §1 | Low-Medium (hygiene + risk of false assumption) |
| `database/` folder contents could not be enumerated by available tooling — unknown whether it already contains partial/conflicting work | §1 | Medium (verify before M1) |
| `06_ERD.md`'s 78-table count vs. `07_DATABASE_SPEC.md` coverage was not exhaustively line-by-line reconciled in this audit | §6.8 | Low-Medium (verify before M1) |
| `03_Business_Rules.md` appears truncated; unknown how much of the BRF rule set is actually missing | §6.7 | Medium |
| Frontend and bots have no roadmap task entries at all — risk of being silently deprioritized or forgotten | §3.1, §7 | Low-Medium |

---

## 10. First Implementation Milestone

Per the roadmap (`10_Development_Roadmap.md`), the next task is literally **"Task 2 — Database."** However, per this audit, that task is **blocked** by an unclosed governance gate: no technology-stack ADR exists, and several document conflicts (§6.2, §6.3) directly affect what the physical schema should enforce.

**Recommended first milestone is therefore M0, not M1:**

> **M0 — Documentation Reconciliation & Technology-Stack ADR**
> 1. Resolve the stock-transfer confirmation contradiction (§6.2) — pick one model and update either `04_Business_Policies.md` or `02_SRS.md`/`06_ERD.md`/`07_DATABASE_SPEC.md` accordingly.
> 2. Tighten the invoice-immutability invariant (§6.3) to match the documented lifecycle, or clarify that INV-003 refers only to post-issuance state.
> 3. Decide whether `Domains.md` is superseded by `02_SRS.md` §14.1's 7-bounded-context list, and update it (or formally deprecate it) accordingly.
> 4. Either populate `05_Domain_Model.md` or add a decision record in `09_Decisions.md` stating that `06_ERD.md` §0/Part J is the authoritative domain model going forward.
> 5. Confirm with the repository owner whether `03_Business_Rules.md` is intentionally short or missing content.
> 6. Ratify a technology-stack ADR in `09_Decisions.md`: backend language/framework, ORM, migration tool, and confirm PostgreSQL formally (currently only de facto implied).
> 7. Manually inspect the `database/` folder's actual contents (not visible to this audit) and reconcile with `07_DATABASE_SPEC.md` before schema work begins.

Once M0 is closed, the first **code** milestone (M1 in §8) is: **implement the PostgreSQL reference and master-data schema, followed immediately by the Inventory Ledger (`inventory_transaction` + hash-chain + append-only enforcement) as its own isolated, heavily-tested unit**, per the SRS's own explicit risk-mitigation guidance — before any Sales/Order logic is touched.

---

*No application architecture was modified in the production of this audit. No requirements were invented beyond what is stated in the 14 source documents. This document is ready for review before any implementation work begins.*
