# ADR-011: Bilingual (Persian/English) Support

**Status:** Accepted
**Date:** 2026-08-31
**Deciders:** System Architect
**Supersedes:** None
**Related:** ADR-010 (Frontend Technology Stack), `02_SRS.md` §14.2

---

## 1. Context

The entire frontend (8 milestones, ~85 tests) was built English-only with
hardcoded strings throughout. The application is a Persian accounting ERP
intended for Iranian business users. Retrofitting internationalization (i18n)
is a large, cross-cutting change that touches every screen — not a single
milestone. This ADR establishes the i18n architecture, translation strategy,
RTL handling, calendar system, and number formatting before any implementation
begins.

### What already exists

- React 19 + TypeScript 5 + Vite 6 frontend under `frontend/`
- 85 Vitest + RTL tests across 9 test files
- shadcn/ui + Tailwind CSS 4 component kit
- Hardcoded English strings in all pages, nav items, error messages, and
  placeholder text
- No `components/` directory — all UI lives under `src/features/`
- Tailwind classes are predominantly direction-agnostic (`flex`, `items-center`,
  `justify-between`, `gap-*`, `px-*`/`py-*`). Audit found only **2 instances**
  of physical directional properties (`pr-4` in `ReportRunDetailPage.tsx`).
  No `pl-*`, `ml-*`, or `mr-*` found in component code (one `pl-1` false
  positive in test fixture data).

### What does not exist

- Any i18n library or translation infrastructure
- Any translation files or key management
- Any RTL document direction handling
- Any date formatting abstraction (raw `Date` objects rendered via
  `toLocaleDateString()` or `new Date()`)

---

## 2. Decision: i18n Library

### Chosen: `react-i18next`

| Aspect | Choice | Rationale |
|--------|--------|-----------|
| **Library** | `react-i18next` | Mature, well-maintained, works with React 19 + Vite. Supports namespace-based code-splitting for lazy-loading translation files per feature. Largest i18n ecosystem in the React community. |
| **Bundling** | Per-namespace JSON files | Translation files are co-located with feature directories, not one monolithic flat file. Loaded on demand via `i18next-http-backend` or statically imported. |
| **Type safety** | `i18next` type augmentation | Typed `t()` function via declaration merging on `react-i18next`. |

**Rejected alternatives:**

- **`react-intl` (FormatJS):** Heavier, ICU message format adds complexity for
  this use case. Less idiomatic with React hooks than `react-i18next`.
- **`lingui`:** Requires compile-time extraction step. Overkill for a two-language
  ERP app with relatively simple string substitution needs.
- **Manual context + `useTranslation` custom hook:** Reinvents the wheel. No
  lazy-loading, no pluralization, no namespace support. Not maintainable at
  85+ screens.

---

## 3. Decision: Translation Key Structure

### Chosen: Namespace-per-feature, mirroring `src/features/` folder structure

```
frontend/src/i18n/
├── locales/
│   ├── en/
│   │   ├── common.json        # Shared strings (nav, auth, generic buttons)
│   │   ├── login.json         # LoginPage
│   │   ├── dashboard.json     # RepDashboardPage
│   │   ├── orders.json        # Order domain
│   │   ├── customers.json     # Customer domain
│   │   ├── invoices.json      # Invoice domain
│   │   ├── payments.json      # Payment domain
│   │   ├── credit-notes.json  # Credit Note domain
│   │   ├── inventory.json     # Inventory domain
│   │   ├── transfers.json     # Transfer domain
│   │   ├── reports.json       # Report domain
│   │   ├── kpi.json           # KPI Dashboard
│   │   └── commissions.json   # Commission domain
│   └── fa/
│       ├── common.json
│       ├── login.json
│       └── ... (same structure)
└── index.ts                   # i18next init + export
```

**Key naming convention:** `feature.entity.field` — e.g.
`orders.detail.grandTotal`, `common.nav.orders`, `login.title`.

**Why namespaces:** Each feature's translations can be lazy-loaded only when
that feature's routes are visited, keeping initial bundle small. This also
maps 1:1 to the existing `src/features/` directory structure, making it
obvious where new strings belong.

**Backend-sourced strings:** Enum-like values (order states, permission codes,
reason codes, movement types, etc.) flow from the backend untranslated. The
frontend maintains its own translation map keyed by the backend's existing
string codes — no backend changes needed. For example:

```json
{
  "orderStates": {
    "DRAFT": "پیش‌نویس",
    "APPROVED": "تأیید شده",
    "SHIPPED": "ارسال شده"
  }
}
```

This is the correct call because retrofitting label translation into the
backend would require changing every schema response, adding a `locale`
parameter to dozens of endpoints, and modifying the bootstrap seed data
— massive churn for a frontend-only display concern.

---

## 4. Decision: RTL Handling

### Chosen: Migrate to logical Tailwind properties project-wide

**Current state:** The codebase audit found only **2 instances** of physical
directional properties (`pr-4`) across the entire frontend. The overwhelming
majority of the code uses direction-agnostic utilities (`flex`, `items-center`,
`justify-between`, `gap-*`, `px-*`/`py-*`). This means the migration cost
is extremely low.

**Decision: Migrate the 2 `pr-4` instances to `pe-4` (end-padding).**

| Approach | Pros | Cons |
|----------|------|------|
| **Logical properties (ps-/pe-/ms-/me-)** ✅ | Correct long-term; works automatically with `dir="rtl"`; no per-component maintenance | Requires auditing and migrating existing physical properties |
| **RTL variant prefixes (rtl:/ltr:)** | Smaller per-component diffs | More error-prone; easy to miss cases; ongoing maintenance burden; every new component needs explicit RTL consideration |

Since the codebase already has almost zero physical directional properties,
migrating to logical properties is trivially cheap and permanently correct.

**Document direction toggling:** `<html dir="rtl" lang="fa">` when Persian
is active, `<html dir="ltr" lang="en">` when English is active. This is
handled in the i18n initialization hook — a single `document.documentElement`
mutation on language change.

**shadcn/ui component RTL behavior:** shadcn/ui components are copied into
the project (owned by codebase). Each component uses Tailwind utilities for
layout, which respect `dir` attribute when using logical properties. The
component audit during Phase 1 will verify that the pilot screens' shadcn/ui
components render correctly under RTL. Any physical properties found in
copied shadcn/ui components will be migrated to logical properties at the
same time.

**Tailwind CSS 4 RTL support:** Tailwind CSS 4 supports `rtl:` and `ltr:`
variant prefixes natively. These are available as a fallback for any
components that genuinely need direction-specific overrides (e.g., icon
mirroring). However, the primary strategy remains logical properties, not
variant prefixes.

---

## 5. Decision: Jalali (Solar Hijri) Calendar

### Chosen: Jalali display formatting for Persian-locale users

**User decision (explicitly requested and confirmed):** Persian-locale users
see dates in Jalali (Solar Hijri) format. This is a real business/legal
requirement for an Iranian accounting application — invoices, due dates,
report periods, and KPI history must display in the calendar system used by
Iranian businesses and the Iranian tax authority.

**Implementation:**
- **Date storage stays Gregorian/ISO 8601** in the database and all API
  responses. No schema changes. No backend changes.
- **Display formatting only changes** in the frontend. A `formatDate()` utility
  function wraps the Jalali formatting logic, switching based on the active
  language.
- **Library:** `date-fns-jalali` (or `moment-jalaali` if date-fns-jalali
  proves insufficient for edge cases). The exact library choice is validated
  during Phase 1 implementation.
- **Scope of Jalali formatting:** All user-visible dates — order dates,
  invoice issued/due dates, payment timestamps, KPI captured_at, report
  run timestamps, transfer dates, credit note dates.
- **ISO 8601 strings from the API** are parsed to `Date` objects, then
  formatted via the `formatDate()` utility. No timezone conversion is needed
  (all timestamps are UTC; display formatting is purely cosmetic).

**Why not Gregorian for everyone:** Iranian accounting regulations and
business conventions require Jalali dates. An Iranian accountant looking at
an invoice due date needs to see it in the calendar they use for filing
and payment scheduling.

---

## 6. Decision: Number Formatting

### Chosen: Financial figures always render in Latin digits (0-9), regardless of language

| Context | Format | Rationale |
|---------|--------|-----------|
| **Financial amounts** (order totals, invoice amounts, balances, KPI values) | Latin digits with locale-appropriate separators | Standard practice in Iranian accounting software. Financial figures must be unambiguous — Persian digits (۰-۹) in financial contexts cause confusion when mixed with Latin input, copy-paste, and external system integration. |
| **Quantities** (order line qty, inventory qty) | Latin digits | Same rationale — quantities feed into financial calculations. |
| **Non-financial text** (dates, page labels, descriptions) | Follow locale formatting | Dates use Jalali calendar for Persian. General text uses locale-appropriate numeral rendering where it appears naturally. |

**Implementation:** A `formatNumber()` utility wraps `Intl.NumberFormat` or
a manual formatter. Financial contexts always call `formatNumber()` with
Latin digit output. The locale affects separators (comma vs. comma with
thousands grouping) but never the digit characters themselves for financial
figures.

---

## 7. Decision: Default Language & Persistence

### Chosen: Browser detection → localStorage persistence → per-device preference

**Default language on first load:**
1. Check `localStorage` for a previously saved language preference.
2. If none, detect from `navigator.language` / `navigator.languages`.
   - If the browser language starts with `fa` → default to Persian.
   - Otherwise → default to English.
3. The language switcher in `AppShell` allows toggling at any time.

**Persistence mechanism:** `localStorage` key `app_language`.

**Why localStorage, not a backend field:**
- `CurrentUserResponse` has no `language` or `locale` field.
- `AppUser` ORM model has no language preference column.
- Adding a backend field requires: schema migration, API endpoint update,
  `CurrentUserResponse` change, frontend type regeneration — all for a
  display preference that doesn't affect business logic or data integrity.
- `localStorage` is sufficient for a per-device, per-user preference in a
  web app. If multi-device sync is ever needed, the backend field can be
  added in a future ADR without breaking changes.
- **Electron desktop app (ADR-012):** The desktop shell uses `electron-store`
  for persistent config, which maps to the OS app-data directory. The i18n
  layer reads from the same abstraction — `localStorage` in the browser,
  `electron-store` in the desktop shell.

**Language switcher location:** In the `AppShell` sidebar, near the "Sign out"
button. A simple toggle button showing the other language's name
(English → "فارسی", Persian → "English").

---

## 8. Backend-Sourced String Translation

### Decision: Frontend maintains its own translation map, no backend changes

The backend returns enum-like string codes throughout:
- Order states: `DRAFT`, `APPROVED`, `SHIPPED`, etc.
- Permission codes: `ORDER_MANAGE`, `INVENTORY_MANAGE`, etc.
- Reason codes: `PRICING_ERROR`, `DAMAGED_GOODS`, etc.
- Movement types: `PURCHASE`, `SALE`, `ADJUSTMENT`, etc.
- Credit note states: `DRAFT`, `ISSUED`, `APPLIED`, `VOID`

**The frontend maintains translation maps keyed by these exact string codes.**
No backend changes are needed — no new `label` fields on reference tables, no
`locale` query parameters, no translation endpoints.

**Why:** Retrofitting label translation into the backend would require:
1. Adding a `translation` table and FK from every reference table
2. Adding `locale` parameters to every catalog/reference endpoint
3. Modifying bootstrap seed data to include translations
4. Changing every Pydantic response schema to include translated labels
5. Updating the OpenAPI schema and regenerating frontend types

This is massive backend churn for a frontend-only display concern. The
frontend translation map is the right layer for this.

---

## 9. Scope: Phase 1 (This Pass)

Phase 1 is a **foundation and pilot**, not a full retrofit.

### Deliverables

1. **i18n scaffold:**
   - Install `react-i18next`, `i18next`, `i18next-http-backend` (or static imports)
   - Create `src/i18n/index.ts` with i18next initialization
   - Create locale directory structure: `src/i18n/locales/{en,fa}/`
   - Create `common.json` (nav labels, generic buttons, auth strings)
   - Add `LanguageProvider` wrapper in `App.tsx`
   - Implement `dir` toggling on language change (`<html dir="rtl"|"ltr">`)

2. **Language switcher:**
   - Add toggle button in `AppShell` sidebar
   - Persist choice to `localStorage`
   - Detect browser default on first load

3. **Pilot screens (fully translated + RTL-verified):**
   - `LoginPage` — form labels, button text, error messages
   - `AppShell` — nav items, "Sign out", shell label, username display
   - `RepDashboardPage` — card headings, links, empty states, recent orders

4. **Backend-sourced string translations:**
   - `orderStates` translation map for RepDashboardPage's state badges
   - `common` translations for nav items

5. **RTL verification:**
   - All three pilot screens verified under `dir="rtl"`
   - The 2 existing `pr-4` instances migrated to `pe-4`

6. **Tests:**
   - Test asserting language switcher changes `dir` attribute
   - Test asserting translated text renders for piloted screens

### Explicitly out of scope (Phase 1)

- All other screens (Orders, Customers, Invoices, Payments, Credit Notes,
  Inventory, Transfers, Reports, KPI, Commissions) remain English-only
  with TODO/roadmap notes
- Jalali date formatting library installation (deferred to the date-formatting
  phase — the pilot screens have minimal date display)
- Full shadcn/ui component RTL audit (deferred to per-feature rollout)
- Backend changes of any kind

---

## 10. Future Phases (Not Implemented in This Pass)

| Phase | Scope | Dependency |
|-------|-------|------------|
| Phase 2 | Jalali date library integration + date formatting utility | Phase 1 scaffold |
| Phase 3 | Per-feature translation rollout (one feature per pass, starting with Orders since it's the most complex) | Phase 1 scaffold |
| Phase 4 | shadcn/ui component RTL audit + full logical-property migration | Phase 3 (done per-feature) |
| Phase 5 | Number formatting utility (financial figures in Latin digits) | Phase 1 scaffold |
| Phase 6 | Backend string translation map completion (all enum values) | Phase 3 |

---

## 11. Schema Changes

NONE. All date storage stays Gregorian/ISO 8601. All backend responses remain
unchanged. The frontend handles all display formatting.

---

## 12. Production Code Changes

Phase 1 only (ADR itself documents the full architecture):
- New `src/i18n/` directory with initialization and locale files
- Modified `App.tsx` to wrap with i18n provider
- Modified `AppShell.tsx` to add language switcher and use translated nav items
- Modified `LoginPage.tsx` to use translated strings
- Modified `RepDashboardPage.tsx` to use translated strings
- Fixed 2x `pr-4` → `pe-4` in `ReportRunDetailPage.tsx`
