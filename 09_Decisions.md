## ADR-012

Desktop Packaging (.exe) — Electron shell around existing web frontend.

Status: Accepted
Date: 2026-08-31

See ADR-012-Desktop-Packaging.md for the full decision record.

Summary of decisions:

1. **Shell:** Electron + electron-builder (NSIS Windows target). Separate
   `desktop/` package that builds the existing `frontend/` and wraps it.
   No merged configs.

2. **Backend URL:** First-run settings screen (standalone HTML, not React)
   where user enters backend base URL. Persisted via `electron-store` in
   OS app-data directory. React app reads URL from Electron context bridge,
   falls back to `VITE_API_BASE_URL` for web deployment.

3. **Security:** `contextIsolation: true`, `nodeIntegration: false`,
   `sandbox: true`. Minimal IPC surface (get-config / set-config only).
   JWT auth stays client-side against remote API.

4. **Auto-update:** Out of scope — manual .exe distribution for now.
   Flagged as future ADR.

5. **Code signing:** Out of scope — unsigned .exe, SmartScreen warning
   expected.

Schema changes: NONE.
Production code changes: `frontend/src/api/client.ts` (backward-compatible
Electron URL support).

---

## ADR-011

Bilingual (Persian/English) Support — i18n architecture and RTL handling.

Status: Accepted
Date: 2026-08-31

See ADR-011-Bilingual-Support.md for the full decision record.

Summary of decisions:

1. **Library:** react-i18next with namespace-per-feature translation files
   mirroring `src/features/` folder structure.

2. **RTL:** Migrate 2 existing `pr-4` instances to `pe-4` (logical
   properties). Codebase is already 99%+ direction-agnostic. `<html dir>`
   toggling on language change.

3. **Calendar:** Jalali (Solar Hijri) for Persian-locale users. Date
   storage stays Gregorian/ISO. Display formatting only changes.

4. **Numbers:** Financial figures always in Latin digits (0-9), regardless
   of language. Standard Iranian accounting practice.

5. **Persistence:** `localStorage` for language preference (per-device).
   Browser language detection on first load.

6. **Backend strings:** Frontend maintains its own translation map keyed
   by backend enum codes. No backend changes needed.

Schema changes: NONE.
Production code changes: New `src/i18n/` directory, modified App.tsx,
AppShell.tsx, LoginPage.tsx, RepDashboardPage.tsx.

---

## ADR-010

Frontend Technology Stack — Framework, UI Kit, API Client Generation, Auth Flow.

Status: Accepted
Date: 2026-08-30

See ADR-010-Frontend-Technology-Stack.md for the full decision record.

Summary of decisions:

1. **Framework:** React 19 + TypeScript 5.x + Vite 6 + React Router v7.
   Ratifies (with refinements) SRS §14.2 recommendation. SSR frameworks
   (Next.js, Remix) rejected — unnecessary complexity for an internal
   enterprise SPA.

2. **Data layer:** TanStack Query v5 for server state; React Context for
   auth/UI state. API client auto-generated from backend OpenAPI schema
   via `openapi-typescript` + `openapi-fetch` (type-safe, no codegen
   overhead). No hand-written request/response types.

3. **UI kit:** shadcn/ui + Tailwind CSS 4. Components are copied into the
   project (owned by codebase, not an external dependency). Lucide React
   icons. TanStack Table v8 for data grids.

4. **Auth:** JWT Bearer token stored in localStorage; attached to all
   requests via shared fetch wrapper. `GET /rbac/me/permissions` drives
   UI-level feature gating (UX-only — backend remains sole authorization
   source of truth).

5. **Single codebase, role-routed:** Same React app serves admin/office
   UI (A1,A2,A5,A6) and representative portal (A4,A7) via /office/* and
   /rep/* route prefixes. Server-side representative scope (ADR-007)
   handles data filtering.

6. **Folder structure:** src/api/, src/features/, src/components/, src/lib/,
   src/routes/ — mirrors backend's api/services/dependencies separation.

Schema changes: NONE.
Production code changes: NONE (ADR only; scaffold is next milestone).

---

## ADR-007

Representative Data Scope authorization pattern.

Every consumer that reads data on behalf of a bound representative
(bot commands, API endpoints, reporting, future representative
portal) must enforce the same scope rules through a single shared
service layer: ``services/representative_scope_service.py``.

Scope resolution:

1. **Representative → Customer scope** is resolved through
   ``customer_rep_assignment`` (C6). A customer is "assigned to" a
   representative when the assignment row's ``effective_from <= at``
   AND (``effective_to IS NULL`` OR ``effective_to > at``), where
   ``at`` defaults to ``datetime.now(timezone.utc)``. Multiple
   simultaneously-effective assignments for the same customer are
   ranked by ``priority`` (ascending = highest priority first).

2. **Representative → Warehouse scope** is resolved through
   ``warehouse_assignment`` (C5). A warehouse is "assigned to" a
   representative when the assignment row's ``effective_from <= at``
   AND (``effective_to IS NULL`` OR ``effective_to > at``). The
   ``is_primary`` flag identifies the representative's primary
   warehouse; callers may request ``primary_only=True`` to retrieve
   only the primary warehouse.

3. **Order authorization** is enforced at the service layer:
   ``order_service.get_order_for_representative()`` fetches an order
   by ID and rejects access when
   ``order.representative_id != requested_representative_id``. This
   prevents cross-representative data leakage through direct ID
   access.

4. **Cross-representative access prohibition**: No scope function
   returns data belonging to a different representative. Every query
   is anchored to the representative's own assignment rows. The bot
   session's ``representative_id`` (from ``bot_session`` M12) is the
   sole identity anchor.

5. **Scope enforcement location**: The scope service lives in the
   domain/service layer, NOT in the Telegram adapter, NOT in bot
   command handlers, and NOT in any platform-specific code. All
   consumers (bot, API, reporting, future portal) call the same
   functions.

6. **Scope functions do NOT duplicate assignment or business rules**.
   They read through the existing ``customer_rep_assignment`` and
   ``warehouse_assignment`` tables, respecting their time-window
   semantics and priority ordering. No new constraints, triggers, or
   columns are added to these tables for scoping purposes.

Scope functions added in this milestone:

- ``resolve_representative_customers(session, representative_id, at=None)``
- ``resolve_representative_warehouses(session, representative_id, at=None, primary_only=False)``
- ``order_service.get_order_for_representative(session, order_id, representative_id)``

Out of scope for this ADR (deferred to future decisions):

- Balance command semantics (how to present AR balance per customer)
- Inventory command semantics (which products, which warehouse)
- Customer selection UX (single vs. list, filtering)
- Warehouse selection rules (primary vs. all vs. ask)
- Write operations (BOT_WRITE permission not implemented)

Reason:

The ERD's ``bot_session`` (M12) business constraints state "commands
scoped by this binding; no cross-rep access." SRS §15.5 states "Keep
bot adapters thin; identity resolution centralized and scoped." The
domain model already defines the assignment tables (C5, C6) but no
service-layer resolution existed. This ADR fills that gap with a
single, reusable scope layer.

Status:

Accepted
