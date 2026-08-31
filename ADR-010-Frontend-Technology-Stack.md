# ADR-010: Frontend Technology Stack

**Status:** Accepted
**Date:** 2026-08-30
**Deciders:** System Architect
**Supersedes:** None
**Related:** ADR-007 (Representative Data Scope), ADR-004 (Order State Machine), `02_SRS.md` §14.2

---

## 1. Context

The backend API (FastAPI + PostgreSQL) is well into its delivery cycle — all
seven bounded contexts have live endpoints, the RBAC/permission infrastructure
is production-tested, and multiple security-hardening passes have closed
IDOR/authorization gaps. The `IMPLEMENTATION_AUDIT.md` flagged that "frontend
has no roadmap task at all" and no ADR selects a frontend framework. `02_SRS.md`
§14.2 only *recommends* "React + TypeScript + Vite + TailwindCSS, state via
TanStack Query; admin UI via shadcn/ui" — which per `CLAUDE.md`'s "never
generate code before design approval" rule is not sufficient to start writing
frontend code.

This ADR ratifies (with minor refinements) the SRS §14.2 recommendation and
establishes the technical foundation for the `frontend/` directory.

### What already exists

- Backend FastAPI app serving `/api/v1/*` with OpenAPI schema at `/openapi.json`
- JWT bearer authentication (`POST /auth/login` → `TokenResponse` with
  `access_token` + `expires_in`)
- Permission-based RBAC: `GET /rbac/me/permissions` returns the caller's
  effective permission codes
- Role-linked representative identity: `AppUser.representative_id` drives all
  server-side scope enforcement (ADR-007)

### What does not exist

- Any `frontend/` directory or frontend build tooling
- Any frontend-related `package.json`, `tsconfig.json`, or build config
- Any design system or component library integration
- Any CORS configuration for a frontend dev server

---

## 2. Decision: Framework & Build Tool

### Ratified: React 19 + TypeScript + Vite

| Aspect | Choice | Rationale |
|--------|--------|-----------|
| **UI library** | React 19 | Mature ecosystem, largest talent pool, component model maps well to domain-driven UI composition |
| **Language** | TypeScript 5.x | Type safety across components, hooks, and the generated API client; catches schema drift at compile time |
| **Build tool** | Vite 6 | Fast dev server (ESM-native HMR), production builds via Rollup, minimal config; dominates the React ecosystem since 2023 |
| **Routing** | React Router v7 (file-based) | De facto standard for React SPAs; file-based routing reduces boilerplate; nested routes map to layout shells |
| **State management** | TanStack Query v5 (server state) + React Context (auth/UI state) | TanStack Query is the canonical server-state cache for REST APIs; React Context suffices for auth token/permissions (small, rarely-changing state). No Redux/Zustand needed at this scale |

**Rejected alternatives:**

- **Next.js / Remix (SSR frameworks):** The backend is a separate FastAPI service. The frontend is a pure SPA consumed by office staff and representatives on known domains. SSR adds complexity (server deployment, hydration, streaming) without meaningful SEO or performance benefit for an internal enterprise tool. If SSR is ever needed, React Router v7's own SSR mode can be adopted without rewriting components.
- **Vue / Angular / Svelte:** The SRS already recommends React. No evidence of team expertise in alternatives. React's ecosystem breadth (shadcn/ui, TanStack Query, React Router) is unmatched for this use case.

---

## 3. Decision: API Client Generation

### Decision: Auto-generate from OpenAPI via `openapi-typescript`

The backend exposes a live OpenAPI schema at `GET /openapi.json` (FastAPI's
built-in Swagger/ReDoc endpoint). Rather than hand-writing request/response
types that duplicate the backend Pydantic schemas, the frontend will generate
TypeScript types directly from the OpenAPI spec.

| Aspect | Choice | Rationale |
|--------|--------|-----------|
| **Generator** | `openapi-typescript` (npm) | Zero-runtime, type-only generation; produces `paths`, `components`, and `operations` types from any OpenAPI 3.x spec; widely adopted |
| **Integration** | `openapi-fetch` (same ecosystem) | Type-safe `fetch` wrapper that infers request params and response types from the generated schema; no codegen overhead, no RPC magic |
| **Script** | `npm run api:gen` | Runs `openapi-typescript http://localhost:8000/api/v1/openapi.json -o frontend/src/api/types.d.ts` — re-runnable whenever the backend schema changes |
| **Versioning** | Generated types are committed to git | Ensures CI/builds work without a running backend; PR diffs show schema changes clearly |

**Rejected alternatives:**

- **Orval:** Heavier, generates more code (full client hooks per endpoint), opinionated about React Query integration. `openapi-typescript` + `openapi-fetch` is more transparent and less coupled to any specific data-fetching library.
- **RTK Query codegen / tRPC:** Requires additional backend setup (tRPC) or Redux (RTK Query), neither of which fits this project's stack.
- **Hand-written types:** Per the user's instruction — do not duplicate backend Pydantic schemas in TypeScript. This is a maintenance burden that will drift.

---

## 4. Decision: UI Component Kit

### Ratified: shadcn/ui + Tailwind CSS 4

| Aspect | Choice | Rationale |
|--------|--------|-----------|
| **CSS framework** | Tailwind CSS 4 | Utility-first CSS; eliminates CSS architecture debates; consistent spacing/typography tokens; works seamlessly with shadcn/ui |
| **Component kit** | shadcn/ui | Not a dependency — components are copied into the project and owned by the codebase. This means: (a) full control over styling and behavior, (b) no version-lock to an external package, (c) components can be customized to match enterprise domain terminology |
| **Icons** | Lucide React (shadcn/ui default) | Consistent icon set, tree-shakeable, matches shadcn/ui's visual language |
| **Table/data grid** | TanStack Table v8 | Headless table primitive; pairs with TanStack Query for server-side pagination/sorting/filtering; shadcn/ui provides pre-built table styling |

**Rejected alternatives:**

- **Material UI / Ant Design:** Heavy, opinionated, harder to customize. Enterprise ERPs need custom domain components, not generic widget libraries.
- **Radix UI directly (without shadcn/ui):** shadcn/ui is built on Radix primitives but adds Tailwind-styled defaults, reducing initial setup time. Since we own the code either way, starting from shadcn/ui's component collection is faster.

---

## 5. Decision: Authentication & Authorization Flow

### 5.1 Token Storage

| Concern | Decision |
|---------|----------|
| **Storage mechanism** | `localStorage` key `auth_token` — accessible to all tabs/windows, persists across browser restarts |
| **Token format** | JWT Bearer token (already issued by `POST /auth/login`) |
| **Token attachment** | Every API request includes `Authorization: Bearer <token>` via a shared fetch wrapper |
| **Token refresh** | Not implemented in this milestone. Token lifetime is 8 hours (`access_token_expire_minutes: 480`). When refresh tokens are added backend-first, the frontend wrapper will handle 401 → refresh → retry transparently |
| **Logout** | Client-side only: remove `auth_token` from `localStorage`, redirect to `/login`. No server-side token revocation exists yet |

**Rejected alternatives:**

- **httpOnly cookies:** Requires backend changes to set cookie headers instead of returning a JSON body. The backend's `POST /auth/login` returns `TokenResponse(access_token=..., expires_in=...)` — retrofitting cookie flow is a separate backend ADR.
- **In-memory only (no persistence):** Would force re-login on every page refresh. Unacceptable UX for office users who keep the app open all day.

### 5.2 Permission-Based UI Gating

The backend's `GET /rbac/me/permissions` returns the caller's effective
permission codes (e.g., `["ORDER_MANAGE", "INVENTORY_MANAGE",
"PRODUCT_MANAGE"]`). The frontend uses this list to conditionally render
UI elements (nav items, buttons, action menus).

**Critical constraint:** This is UX-level gating only. The backend remains the
sole authorization source of truth. Every `require_permission()` and
scope-check in the backend enforces the real gate. The frontend hides controls
to avoid user confusion, not to enforce security.

**Implementation:**

```typescript
// Auth context provides:
interface AuthContext {
  token: string;
  user: CurrentUser;          // GET /auth/me
  permissions: Set<string>;   // GET /rbac/me/permissions
}

// Permission check hook:
function usePermission(code: string): boolean {
  const { permissions } = useAuth();
  return permissions.has(code);
}

// In components:
{hasPermission('ORDER_MANAGE') && <NavLink to="/orders">Orders</NavLink>}
```

### 5.3 Role-Based Routing

Per `02_SRS.md` §2.1: "Same React app, role-routed (responsive/PWA)."

| Route prefix | Target actors | Navigation |
|-------------|---------------|------------|
| `/` (root) | Redirects based on permissions | Auto-redirect to highest-capability view |
| `/office/*` | A1 (Admin), A2 (Sales Mgr), A3 (Warehouse), A5 (Finance), A6 (Report Viewer) | Full admin sidebar navigation |
| `/rep/*` | A4 (Representative), A7 (Bot User via web) | Simplified rep portal view |

The router checks `user.representative_id` (from `GET /auth/me`) to determine
portal mode. Admin/office users (no `representative_id`) see the full admin
shell. Representative-linked users see the rep portal shell. Both shells share
the same generated API client, auth context, and component library.

---

## 6. Decision: Folder & Layer Structure

The frontend directory mirrors the backend's separation philosophy
(`api/services/repositories/schemas/core/dependencies`) adapted to frontend
idioms:

```
frontend/
├── src/
│   ├── api/                    # API layer (replaces backend/api/)
│   │   ├── types.d.ts          # Generated from OpenAPI schema
│   │   ├── client.ts           # openapi-fetch client instance (base URL, auth header)
│   │   └── hooks/              # TanStack Query hooks wrapping API calls
│   │       ├── useOrders.ts    # useQuery/useMutation per domain entity
│   │       ├── useCustomers.ts
│   │       └── ...
│   │
│   ├── features/               # Domain feature modules (replaces backend/services/)
│   │   ├── auth/               # Login page, auth context, permission hooks
│   │   ├── layout/             # App shell, sidebar, header, breadcrumb
│   │   ├── customers/          # Customer list, detail, forms (future milestone)
│   │   ├── orders/             # Order list, detail, workflow (future milestone)
│   │   ├── inventory/          # Inventory views (future milestone)
│   │   └── ...
│   │
│   ├── components/             # Shared UI components (replaces a hypothetical UI lib)
│   │   ├── ui/                 # shadcn/ui components (Button, Dialog, Table, etc.)
│   │   ├── data-table.tsx      # Reusable TanStack Table + shadcn wrapper
│   │   └── ...
│   │
│   ├── lib/                    # Utilities & helpers (replaces backend/core/)
│   │   ├── utils.ts            # cn() helper, formatters, date utils
│   │   ├── constants.ts        # Permission codes, route paths, status enums
│   │   └── ...
│   │
│   ├── routes/                 # Route definitions (replaces backend/dependencies/)
│   │   ├── __root.tsx          # Root route with auth gate
│   │   ├── login.tsx           # Login page (public)
│   │   ├── office.tsx          # Admin/office layout shell
│   │   └── rep.tsx             # Representative portal layout shell
│   │
│   ├── hooks/                  # Shared custom hooks
│   │   └── usePermission.ts    # Permission check hook
│   │
│   ├── main.tsx                # Entry point
│   └── index.css               # Tailwind imports
│
├── public/                     # Static assets
├── package.json
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts
├── components.json             # shadcn/ui configuration
└── README.md
```

### Layer Correspondence

| Backend layer | Frontend equivalent | Responsibility |
|--------------|-------------------|---------------|
| `app/api/v1/endpoints/` | `src/api/hooks/` | API call definitions (TanStack Query hooks) |
| `app/schemas/` | `src/api/types.d.ts` (generated) | Request/response type definitions |
| `services/` | `src/features/*/` | Domain-specific UI logic, form validation, page composition |
| `app/dependencies/` | `src/routes/` + `src/hooks/` | Auth gating, permission checks, route guards |
| `app/core/` | `src/lib/` | Configuration, utilities, constants |

---

## 7. Decision: Development & Testing

| Concern | Choice |
|---------|--------|
| **Package manager** | pnpm — faster, stricter dependency resolution, disk-efficient |
| **Dev server** | Vite dev server (port 5173) |
| **Test runner** | Vitest — native Vite integration, Jest-compatible API, faster than Jest for Vite projects |
| **Component testing** | React Testing Library — tests user-visible behavior, not implementation details |
| **Linting** | ESLint + Prettier — matching standard React/TypeScript configuration |
| **Environment config** | `.env` file with `VITE_API_BASE_URL=http://localhost:8000` — Vite's `import.meta.env` for client-side env vars |

### CORS Configuration

The backend's `Settings.cors_origins` (currently `[]`) must be widened to allow
the frontend dev server origin:

```
VITE_API_BASE_URL=http://localhost:8000   # backend
Vite dev server runs at http://localhost:5173
→ Backend CORS must include http://localhost:5173
```

This is a configuration-only change to `backend/app/core/config.py`'s default
`cors_origins` or to the `.env` file. No backend code changes required beyond
the env/config value.

---

## 8. Single Codebase, Role-Routed

Per `02_SRS.md` §2.1, the same React application serves both:

1. **Admin/Office UI** (actors A1, A2, A5, A6): Full CRUD for all entities,
   approval workflows, reporting, configuration. Accessed via `/office/*` routes.
2. **Representative Portal** (actors A4, A7): Scoped to the representative's
   own customers, orders, inventory, commissions. Accessed via `/rep/*` routes.

Both views share:
- Same `package.json`, build tooling, and deployment artifact
- Same auth context and token management
- Same generated API client and TanStack Query hooks
- Same UI component library (shadcn/ui + Tailwind)

They differ in:
- Route prefix and layout shell (sidebar navigation depth, available menu items)
- Permission-gated visibility (rep portal shows fewer nav items)
- Data scope (server-side representative scope enforcement per ADR-007 — the
  frontend never needs to filter data client-side; the backend already does it)

---

## 9. Files Changed in This Milestone

| File | Change | Purpose |
|------|--------|---------|
| `ADR-010-Frontend-Technology-Stack.md` | **New** | This ADR |
| `09_Decisions.md` | Modified | Add ADR-010 entry |
| `10_Development_Roadmap.md` / `backend/10_Development_Roadmap.md` | Modified | Add Frontend as explicit roadmap task |
| `frontend/` (entire directory) | **New** | Scaffold: Vite + React + TS + Tailwind + shadcn/ui |
| `frontend/src/api/types.d.ts` | **New** (generated) | TypeScript types from backend OpenAPI schema |
| `frontend/src/api/client.ts` | **New** | openapi-fetch client with auth header injection |
| `frontend/src/features/auth/` | **New** | Login page, auth context, permission hooks |
| `frontend/src/routes/` | **New** | Route definitions with auth gate |
| `backend/app/core/config.py` | Modified (config value) | CORS origins widened for frontend dev server |

---

## 10. What This Milestone Does NOT Implement

- No business-domain screens (Product, Customer, Order, Invoice, Transfer,
  Payment, Commission, Credit Note, Reports, Dashboard)
- No representative portal's distinct views (beyond route shell)
- No bot-related UI
- No offline/PWA support (mentioned in SRS as future)
- No i18n / multi-language support (mentioned in SRS as future expansion)
- No E2E tests (Playwright recommended in SRS §14.2 but deferred to domain
  milestones when there are actual flows to test)

---

## 11. Unresolved Architectural Decisions

1. **PWA / Service Worker:** SRS §2.1 mentions "responsive/PWA" but no
   offline-first requirement exists for v1. Deferred until the representative
   portal's mobile usage patterns are better understood.

2. **Dark mode:** shadcn/ui supports it via CSS variables. Not blocked by any
   decision, but not in scope for Foundation. Can be added at any time.

3. **Backend framework alignment:** This ADR covers frontend only. The backend
   is already built on FastAPI (Python) — if a separate backend tech-stack ADR
   is ever written, it should note that the backend is already implemented.

---

*Generated with Codebuff 🤖*
