# Bot Architecture — Cleanup Report (Phase 16)

Date: 2026-09-03. Companion to `docs/BOT_SETUP.md` and ADR-013 (rev. 2026-09-03).

**Policy applied:** nothing legacy was deleted. The task rules require that
legacy bot code stays unless removal is provably safe; every classification
below is based on a code search at report time, and only code with **no**
remaining references is a deletion candidate.

## Legacy components and status

| Component | Status | Evidence / notes |
|---|---|---|
| `services/bot_command_service.py` (`process_message`, `_find_user_by_representative`, `_register_command`, binding-token session resolution) | **Superseded — kept** | The REST/JWT flow (ADR-013 rev.) does not route through it. Still referenced: ~80 legacy `backend/tests/test_*_command*.py` suites exercise it; `backend/app/dependencies/bot_auth.py` and `backend/app/api/v1/endpoints/bot.py` import `_find_user_by_representative` for audit-actor attribution (2 references). Not used by `bots/` (primary adapters). |
| `services/bot_commands/` (30+ command handlers: `create_order`, `adjust`, `ship`, `create_invoice_cmd`, …) | **Superseded — kept** | Imported only by `bot_command_service.py` and legacy tests. The primary bots never call them. |
| `database/models/bot_message_log.py` + `BotMessageLog` message-level audit | **Indirectly used (legacy)** | Written by the legacy command flow; not written by the new REST flow (which audits via `audit_log`). Model stays (table exists; old rows must remain readable). |
| `database/models/bot_binding_token.py` + binding-token helpers in `bot_session_service.py` (`create_bot_session_with_binding_token` et al.) | **Indirectly used (legacy)** | `services/representative_service.py` (admin "generate link token") + legacy tests reference it. Not part of the phone-verification flow. |
| `telegram_adapter/` (DI adapter around `process_message`) | **Superseded — kept** | Library-style adapter with no in-repo runtime entry point in the primary architecture; referenced by its own docs/tests only. No deletion performed. |
| `bots/shared.py`, `bots/telegram_bot.py`, `bots/bale_bot.py`, `bots/config.py` (pre-integration versions) | **Rewritten in place** | Replaced by the REST adapters (shared API client/keyboards, platform identity, logout) — not duplicated, rewritten at the same paths. |
| `services/bot_phone_service.py`, `backend/app/dependencies/bot_auth.py`, `backend/app/api/v1/endpoints/bot.py` (pre-integration versions) | **Rewritten in place** | Pre-integration versions issued a bare JWT without session binding/RBAC; superseded by the integrated versions at the same paths. |
| `bot_session` (M12) model | **Actively used (extended)** | Used by both legacy and new flows; gained `last_seen`, `expires_at`, `revoked_at`, `revoked_by` (migration `20260903_0000`). |

## What could be removed later (deferred, needs its own step)

A future cleanup step may delete, together and only after confirming the
legacy command tests are retired:

1. `services/bot_commands/` package + all command-handler modules it imports.
2. `services/bot_command_service.py` (after relocating the two
   `_find_user_by_representative` imports or removing legacy audit
   attribution).
3. Binding-token flow: `database/models/bot_binding_token.py`,
   `services/bot_session_service.py` binding-token helpers, the admin
   link-token endpoint in `services/representative_service.py`, and
   `database/models/bot_message_log.py`.
4. The legacy `backend/tests/test_bot_*` command suites **only** once their
   coverage is mapped onto the new REST flow (many rules — scope, RBAC,
   approval, audit — already have REST equivalents in
   `backend/tests/test_bot_phone_verification.py` and the ERP-wide suites).

Nothing above was deleted in this integration. Deletion is intentionally a
separate, documented step.
