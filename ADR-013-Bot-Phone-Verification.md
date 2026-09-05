# ADR-013: Bot Phone-Based Authentication & Bale Integration

**Status:** Accepted
**Date:** 2026-09-02
**Revision:** 2026-09-03 (reality check against executable code)
**Deciders:** System Architect
**Supersedes:** None
**Related:** ADR-007 (Representative Data Scope), ADR-008 (Bot Write Authorization)

---

## 1. Context

The existing bot infrastructure (Phase A) uses admin-generated binding tokens
for identity verification. A representative must ask an administrator to
generate a token, then send `/link <token>` in Telegram. This creates
friction for onboarding.

The business requirement is self-service phone-based authentication:
a representative shares their phone number (already stored in the database
as `representative_contact` with `kind=PHONE`), the bot verifies it, and
grants access to inventory, reports, and invoice creation via inline
keyboard buttons.

Additionally, the system must support Bale (Iranian messaging platform)
alongside Telegram, using the same shared bot logic.

## 2. Design Decisions

### D1: Phone verification via dedicated endpoint

A new `POST /api/v1/bot/verify-phone` endpoint accepts:
- `phone_number` (normalized, E.164-style)
- `platform` (`"telegram"` or `"bale"`)
- `chat_id` (the platform-specific chat identifier)

The endpoint:
1. Normalizes and looks up the phone in `representative_contact` (`kind=PHONE`)
2. Validates the representative is `ACTIVE`
3. Creates **or updates** a persistent `BotSession` row binding
   `(platform, chat_id)` → `representative_id`, stamping `last_seen` and
   `expires_at` (idempotent re-verification)
4. Returns a short-lived JWT (30 minutes) carrying the `representative_id`
   (`sub`) **and the `session_id`** so the auth dependency can reject
   revoked/expired sessions on every request

### D2: Bot sessions reused (not new columns on representative)

Rather than adding `telegram_chat_id` / `bale_chat_id` columns to the
`representative` table, we reuse the existing `bot_session` (M12) table
(gaining `last_seen` / `expires_at` / `revoked_at` fields) and the existing
`bot_session_service`.

**Correction (rev. 2026-09-03):** the original text claimed this "preserves
all 30+ existing command handlers without modification". That is not how the
primary architecture works. The REST/JWT flow (D1/D3) calls the ERP business
services directly through `backend/app/api/v1/endpoints/bot.py`; it does
**not** route through `bot_command_service.process_message()`. The legacy
`bot_command_service` + binding-token flow remains in the codebase (Phase-A
bots) but is superseded by this flow for the new Telegram/Bale adapters.

### D3: JWT tokens for bot API calls

Bot endpoints use a separate JWT flow from the main app auth:
- Short-lived (30 minutes)
- Contains `representative_id` (`sub`) and `session_id`
- Validated by `backend/app/dependencies/bot_auth.py`, which also checks the
  bound `BotSession` is not `REVOKED`/expired and enforces RBAC
  (`BOT_QUERY` for reads, `BOT_WRITE` for writes)
- `rep_id` in URLs is validated against the token (anti-IDOR); scope
  (warehouse/customers/orders) is enforced server-side via the ADR-007
  scope services

### D4: Shared bot logic via the REST API

Telegram and Bale are separate platform adapters (`bots/telegram_bot.py`,
`bots/bale_bot.py`) that share `bots/shared.py` (API client, keyboards,
message formatting) and call the same backend REST endpoints. Neither adapter
duplicates inventory/report/invoice business logic, and neither ever touches
PostgreSQL directly. Bale carries its own platform identity (`platform=bale`,
Bale `chat_id`) — Telegram identity is never assumed to be Bale identity.

### D5: Keyboard buttons map to REST endpoints

Inline keyboard buttons perform authenticated REST calls:

| Button | Endpoint | Permission |
|---|---|---|
| 📦 نمایش موجودی | `GET /bot/reps/{id}/inventory` | `BOT_QUERY` |
| 📊 گزارش عملکرد | `GET /bot/reps/{id}/reports?period=…` | `BOT_QUERY` |
| 🧾 صدور فاکتور | `POST /bot/reps/{id}/invoices` | `BOT_WRITE` |
| 🚪 خروج / قطع اتصال | `POST /bot/logout` | authenticated session |

The invoice action implements the ERP workflow **shipped order → invoice**
via the existing `invoice_service.create_invoice_from_order` (duplicate
invoices rejected). It is not a complete order/sales engine.

### D6: Bot configuration & status owned by the backend

Bot tokens are stored encrypted at rest (`bot_config` rows; Fernet key
derived from `SECRET_KEY`), administered through `Settings → Bots`
(`/api/v1/bot-config/*`), never exposed in full to any frontend, and never
logged. Live status (RUNNING/STOPPED/ERROR/NOT_CONFIGURED) comes from real
heartbeats the bot processes report; it is never faked. `bots/bot_service.py`
is the supervisor that starts the configured platform processes and reports
their state.

## 3. What's Reused vs New

### Reused (adapted where noted)
- `representative` + `representative_contact` models
- `bot_session` model + `bot_session_service` (extended with
  revoke/last_seen/expiry helpers)
- RBAC permission vocabulary (`BOT_QUERY`, `BOT_WRITE`, `BOT_MANAGE`) and the
  audit infrastructure (`audit_service` with `AUTHENTICATE`/`QUERY`/`ATTEMPT`
  actions added)
- ADR-007 scope services (warehouse/customer resolution)
- `inventory_service`, `order_service`, `invoice_service`
  (duplicate-invoice guard added)
- Admin CRUD at `/api/v1/representatives`

### New (implemented)
- `services/bot_phone_service.py` — phone normalization + session binding
- `backend/app/api/v1/endpoints/bot.py` — verify-phone, logout, inventory,
  reports, invoice endpoints (all audited)
- `backend/app/schemas/bot.py` — request/response schemas
- `backend/app/dependencies/bot_auth.py` — JWT + session + RBAC dependency
- `services/bot_config_service.py`, `backend/app/api/v1/endpoints/bot_config.py`
  — encrypted token config, `getMe` connection test, runtime status
- `bots/` adapters (Telegram, Bale, shared), `bots/bot_service.py` supervisor
- `frontend/src/features/bot-settings/BotSettingsPage.tsx` — Persian admin UI
- Migrations: `bot_session` expiry/revoke columns, new `bot_config` table,
  extended audit actions

### Superseded but intentionally kept (Phase-A / legacy)
- `services/bot_command_service.py` and its 30+ command handlers,
  `bots/shared.py` legacy `process_message` path, binding-token session flow,
  `bot_message_log` message-level audit. Removal is a separate,
  documented cleanup step (see final bot-integration report) — the new
  architecture does not route through them, but they are not deleted.

## 4. Security Considerations

- Phones are matched against `representative_contact` (PHONE kind); only
  `ACTIVE` representatives authenticate; phone numbers are not echoed in bot
  responses or audit records.
- Platform identity is bound to the verified representative at verification
  time and persisted in `bot_session` (survives bot restarts).
- JWTs are short-lived (30 minutes) and carry `session_id`; revoked
  (`REVOKED`) and expired sessions are rejected server-side on every request.
- `rep_id` comes from the token, not the URL (IDOR-resistant); data access is
  additionally restricted by ADR-007 scope services.
- Every protected endpoint independently validates: authenticated bot
  session + representative identity + representative scope + required
  permission (`BOT_QUERY`/`BOT_WRITE`) + business rules.
- Bot write operations cannot bypass ERP rules: invoice creation goes through
  `invoice_service` (stock/order-state/duplicate/accounting rules intact).
- Secrets: tokens are encrypted at rest, full values never returned to the
  frontend, never in localStorage, never logged. `.env.example` has
  placeholders only.
- Bot actions are recorded in `audit_log` identifying the
  representative/session/platform; secrets are never written there.

## 5. Files Changed (rev. 2026-09-03)

See the final bot-integration report for the full diff. Highlights:

| File | Change |
|------|--------|
| `backend/app/api/v1/endpoints/bot.py` | Rewritten — verify-phone + logout + scoped/RBAC-gated data endpoints |
| `backend/app/dependencies/bot_auth.py` | Rewritten — token+session validation, RBAC gates |
| `services/bot_phone_service.py` | Session-bound verification |
| `services/bot_session_service.py` | Revoke/last_seen/expiry helpers |
| `services/invoice_service.py` | Duplicate-invoice protection |
| `services/audit_service.py` | `AUTHENTICATE`/`QUERY`/`ATTEMPT` actions |
| `services/bot_config_service.py` + `backend/app/api/v1/endpoints/bot_config.py` | New — config/status endpoints |
| `database/models/bot_session.py`, `database/models/bot_config.py` | Model changes + new table |
| `bots/` (shared, telegram, bale, config, bot_service) | REST adapters + supervisor |
| `frontend/src/features/bot-settings/BotSettingsPage.tsx` | Persian admin UI |
| `.env.example` | Placeholder-only bot vars |
| `docs/BOT_SETUP.md` | Operator/setup guide matching implementation |

---

*Generated with Codebuff*
