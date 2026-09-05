# BOT_SETUP — Telegram / Bale Representative Bots

This guide matches the **executable** state of the code. The bot architecture
is the REST/JWT flow described by ADR-013 (phone verification → persistent
`bot_session` → scoped REST API). The bots never touch PostgreSQL directly;
they talk to the FastAPI backend over HTTP.

```
Telegram / Bale user
    ↓  /start + share phone
POST /api/v1/bot/verify-phone          (binds platform identity to ACTIVE rep)
    ↓  short-lived bot JWT (30 min) carrying representative_id + session_id
GET/POST /api/v1/bot/reps/{rep_id}/…   (scoped + RBAC-gated)
    ↓
Existing ERP services (inventory / order / invoice / scope / audit)
```

---

## 1. Create a Telegram bot token

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, choose a display name and a username ending in `bot`.
3. BotFather replies with an HTTP API token (`123456:ABC-DEF…`). This is
   **secret** — treat it like a password.

## 2. Configure the token in the ERP

The preferred way (backend owns the secret, stored encrypted at rest):

1. Log in to the ERP as an admin with `BOT_MANAGE` permission.
2. Go to **تنظیمات ← رباتها و پیامرسانها** (`/office/bot-settings`).
3. Telegram Bot → paste the token → **ذخیره**.
   - The token is encrypted (Fernet, key derived from `SECRET_KEY`) and only
     the last 4 characters are ever shown again.
4. Toggle **فعال** (enabled), press **تست اتصال** to validate via Telegram's
   `getMe`. On success the bot's real name/username appear automatically
   (from `getMe`); a failed test shows a useful error and never claims the
   bot is connected.

Development fallback: set `TELEGRAM_BOT_TOKEN` in `.env` (see
`.env.example`). The bot process reads the backend-managed token first and
falls back to the env var.

### Telegram network path (Sing-box)

If Windows DNS resolves `api.telegram.org` to an unusable address while
Sing-box TUN is active, configure the existing Sing-box local proxy listener
for the bot process:

```dotenv
TELEGRAM_PROXY=http://127.0.0.1:<sing-box-http-port>
# or: TELEGRAM_PROXY=socks5://127.0.0.1:<sing-box-socks-port>
```

`bots/config.py` reads `TELEGRAM_PROXY`, and `bots/telegram_bot.py` passes it
to aiogram's `AiohttpSession(proxy=...)`. This routes Telegram API traffic
through Sing-box without hard-coding a Telegram IP, changing Windows DNS,
or disabling TLS verification. The variable is Telegram-only; backend API
calls and Bale remain unchanged. Leave it empty when the normal OS/TUN path
works. The port must match the HTTP/SOCKS inbound configured in Sing-box;
this project does not assume or discover a host-specific port.

The token never leaves the backend in responses to normal frontend users,
never goes into React localStorage, and never appears in logs. Do not commit
real tokens — `.env` is git-ignored; `.env.example` has placeholders only.

## 3. Configure Bale

Same steps as Telegram, on the **Bale Bot** card, using a token obtained from
Bale's bot platform. Bale uses its own platform identity (`platform=bale`
plus the Bale chat id) — a Telegram identity is never treated as a Bale
identity.

## 4. Start the bots

Prerequisites: backend running with the DB migrated
(`alembic upgrade head`), and tokens configured (ERP admin UI or env vars).

**Both platforms (recommended):**

```bash
python -m bots.bot_service          # supervisor: starts configured bots,
                                    # reports real status, restarts on crash
python -m bots.bot_service --only telegram
python -m bots.bot_service --only bale
python -m bots.bot_service --check  # show which platforms are configured
```

**A single platform directly:**

```bash
python -m bots.telegram_bot
python -m bots.bale_bot
```

`bots/bot_service.py` supervises each platform as its own child process and
POSTs a real heartbeat to `POST /api/v1/bot-config/{platform}/runtime`
(header `X-Bot-Runtime-Secret`, see `.env.example`). The admin UI status
badge is derived from those reports only — status is never faked.

The supervisor live-syncs to the admin configuration: every ~15s it
re-checks the stored token/enabled state and starts a platform when it
becomes enabled, stops it when disabled, and restarts **only that
platform's child process** when its token changes — so a token entered in
the ERP UI is picked up without editing `.env` or restarting the ERP.

## 5. How representatives authenticate

1. The representative opens the bot and sends `/start`.
2. The bot shows a **share phone** button (Telegram contact / Bale contact).
3. The bot calls `POST /api/v1/bot/verify-phone` with
   `{phone_number, platform, chat_id}`.
4. Backend normalizes the phone, finds an **ACTIVE** representative whose
   `representative_contact` (kind `PHONE`) matches, creates/updates the
   persistent `bot_session` row binding
   `(platform, chat_id, representative_id)` with `last_seen`/expiry, and
   issues a 30-minute JWT containing `representative_id` and `session_id`.
5. The session survives bot-process restarts — it lives in PostgreSQL.
   Re-verification with the same phone/platform/chat simply updates the same
   row (idempotent).

## 6. Representative features supported

Main menu (Persian):

- **📦 نمایش موجودی** → `GET /api/v1/bot/reps/{id}/inventory` — balances for
  the representative's primary permitted warehouse (ADR-007 scope service).
  Requires `BOT_QUERY`.
- **📊 گزارش عملکرد** → `GET /api/v1/bot/reps/{id}/reports?period=this_month`
  — order count, revenue, customer count for `today` / `this_week` /
  `this_month`. Requires `BOT_QUERY`.
- **🧾 صدور فاکتور** → `POST /api/v1/bot/reps/{id}/invoices`. Requires
  `BOT_WRITE`.
- **🚪 خروج / قطع اتصال** → `POST /api/v1/bot/logout` — revokes the session.

Every request is validated server-side: valid bot JWT + non-revoked +
non-expired session + `rep_id` from the URL must equal the token's rep +
`BOT_QUERY`/`BOT_WRITE` permission. The bot UI is never trusted for
authorization, and every action is written to `audit_log`.

## 7. Supported invoice workflow (important — current scope)

The ERP's existing invoice flow is **shipped order → invoice**. The bot
implements exactly that, nothing more:

1. The representative (with a `BOT_WRITE` permission) sends the **order
   number** of one of **their own** shipped orders.
2. `POST /api/v1/bot/reps/{id}/invoices` resolves the order (scoped to the
   representative), then calls the existing
   `invoice_service.create_invoice_from_order`.
3. Result: a `DRAFT` invoice (or later states per the ERP state machine).

The bot does **not** create orders, does not confirm/ship, and does not run a
second invoice engine. Duplicate invoices for the same order are rejected
(`409 Conflict`, `InvoiceAlreadyExistsError`). Stock, order-state, accounting
and audit rules are enforced by the existing invoice/order services.

## 8. How to revoke a bot session

- **Representative logout:** the bot menu's **خروج / قطع اتصال** calls
  `POST /api/v1/bot/logout`, which sets the session to `REVOKED`.
- **Admin:** sessions are `bot_session` rows. An admin can revoke a row (or
  an auditor can disable the representative) — any bot JWT whose `session_id`
  maps to a revoked or expired session is rejected with `401` on the next
  request.

A revoked/expired session never re-authenticates on its own; the
representative must share their phone again to create a fresh session.

## 9. Troubleshooting

| Symptom | Check |
|---|---|
| Bot says "متوقف" in admin UI | Process not running or heartbeat older than 90s. Run `python -m bots.bot_service --check`, start the supervisor. |
| "خطا" status | The bot process crashed; supervisor reports `ERROR`. Read the process stderr. |
| "تنظیم نشده" | No token stored. Save a token via Settings → Bots. |
| Bot answers "unknown user" | The phone isn't registered on an ACTIVE representative (`representative_contact`, kind `PHONE`). |
| `401` on every action | Session revoked/expired or JWT invalid — re-verify the phone. |
| `403` on inventory/report | Representative missing `BOT_QUERY` permission (RBAC) or inactive. |
| `403`/`409` on invoice | Missing `BOT_WRITE`, order not shipped, or an invoice already exists for the order. |
| Bot won't start, "token is not set" | No backend config and no env token. See §2. |
| Can't decrypt stored token | `SECRET_KEY` changed; re-save the token in the admin UI. |
| Real credentials unavailable | E2E over Telegram/Bale cannot run; backend behavior is covered by `backend/tests/test_bot_phone_verification.py` with mocked platform APIs. |

Secrets are never logged: the bot layers and services do not print tokens,
and audit entries carry only platform + representative/session identity.

## 10. Desktop / Electron lifecycle

Opening the Electron app does **not** start the Telegram/Bale bots. The
backend and the bots are separate processes:

```
1. Start ERP backend   (FastAPI + PostgreSQL, `alembic upgrade head`)
2. Start bot processes (python -m bots.bot_service, or per-platform)
3. Admin watches live status in Settings → Bots (real heartbeat, not faked)
```

On Windows, run step 2 in its own terminal/console, or register it as a
scheduled task/service. A bot stopped mid-run keeps its session in the DB, so
restarting the process is transparent to authenticated representatives.

---

See also: `ADR-013-Bot-Phone-Verification.md`, `docs/bot-integration.md`
(API payload examples), `.env.example`.
