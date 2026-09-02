# ADR-013: Bot Phone-Based Authentication & Bale Integration

**Status:** Accepted
**Date:** 2026-09-02
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
- `phone_number` (E.164 format: `+98XXXXXXXXXX`)
- `platform` (`"telegram"` or `"bale"`)
- `chat_id` (the platform-specific chat identifier)

The endpoint:
1. Looks up the phone in `representative_contact` (`kind=PHONE`)
2. Validates the representative is `ACTIVE`
3. Creates or updates a `BotSession` row linking the platform identity
4. Returns a short-lived JWT (30 minutes) containing the `representative_id`

### D2: Bot sessions reused (not new columns on representative)

Rather than adding `telegram_chat_id` / `bale_chat_id` columns to the
`representative` table, we reuse the existing `bot_session` (M12) table.
This preserves all 30+ existing command handlers without modification and
keeps the RBAC flow intact.

### D3: JWT tokens for bot API calls

Bot endpoints use a separate JWT flow from the main app auth:
- Short-lived (30 minutes)
- Contains only `rep_id` (not `user_id`)
- Validated by a FastAPI dependency that extracts `rep_id`

### D4: Shared bot logic via `bots/` package

The `bots/` package contains platform-agnostic handlers. Each platform
(Telegram, Bale) has its own entry point that normalizes platform messages
into a shared format, calls the shared handlers, and formats responses.

Both platforms use **aiogram** with different `base_url` configurations.

### D5: Keyboard buttons map to existing commands

Inline keyboard buttons translate to existing command handlers:
- "📦 موجودی انبار من" → `/inventory` handler
- "📊 گزارش فروش من" → `/orders` + `/balance` handlers
- "🧾 صدور فاکتور جدید" → `/create-invoice` handler

## 3. What's Reused vs New

### Reused (no modification)
- `representative` + `representative_contact` models
- `bot_session` model + `bot_session_service`
- `bot_command_service.process_message()` + all 30+ command handlers
- RBAC permission checks (`BOT_QUERY`, `BOT_WRITE`)
- Approval workflow for write commands
- `bot_message_log` for audit trail
- Admin CRUD at `/api/v1/representatives`

### New
- `services/bot_phone_service.py` — phone verification + JWT
- `backend/app/api/v1/endpoints/bot.py` — verify-phone + data endpoints
- `backend/app/schemas/bot.py` — request/response schemas
- `backend/app/dependencies/bot_auth.py` — JWT validation dependency
- `bots/` package — aiogram-based Telegram + Bale adapters
- `frontend/src/features/admin/SalesRepsPage.tsx` — admin UI
- ADR (this document)

## 4. Security Considerations

- Phone numbers are matched against `representative_contact` (PHONE kind)
- Only `ACTIVE` representatives can authenticate
- JWT tokens are short-lived (30 minutes)
- All bot data endpoints extract `rep_id` from the JWT token (not from URL params)
- Platform identity (chat_id) is bound at verification time
- All bot messages are logged to `bot_message_log`

## 5. Files Changed

| File | Change |
|------|--------|
| `ADR-013-Bot-Phone-Verification.md` | **New** — this ADR |
| `services/bot_phone_service.py` | **New** — phone verify + JWT |
| `backend/app/api/v1/endpoints/bot.py` | **New** — bot endpoints |
| `backend/app/schemas/bot.py` | **New** — schemas |
| `backend/app/dependencies/bot_auth.py` | **New** — JWT dependency |
| `backend/app/api/v1/router.py` | Modified — register bot router |
| `bots/__init__.py` | **New** — package |
| `bots/config.py` | **New** — configuration |
| `bots/shared.py` | **New** — shared handlers + keyboard menus |
| `bots/telegram_bot.py` | **New** — Telegram entry point |
| `bots/bale_bot.py` | **New** — Bale entry point |
| `frontend/src/features/admin/SalesRepsPage.tsx` | **New** — admin UI |
| `frontend/src/features/admin/SalesRepCreatePage.tsx` | **New** — create form |
| `frontend/src/features/admin/SalesRepDetailPage.tsx` | **New** — detail/edit |
| `frontend/src/App.tsx` | Modified — add routes |
| `frontend/src/features/layout/AppShell.tsx` | Modified — add nav link |
| `.env` | Modified — add bot tokens |

---

*Generated with Codebuff*
