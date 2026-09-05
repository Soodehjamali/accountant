# Bot Integration Documentation

## Overview

The Enterprise ERP system supports Telegram and Bale bots, allowing sales representatives to:

1. **Authenticate** via phone number verification
2. **View inventory** (stock levels in their assigned warehouse)
3. **View reports** (sales summary, customer count, monthly revenue)
4. **Create orders** for their scoped customers through a guided multi-step
   conversation (prices are always resolved by the ERP -- never typed by
   the representative)

> **Important — order vs invoice.** The menu button is labeled
> `🧾 صدور فاکتور جدید` (legacy label kept unchanged), but the action
> creates a **DRAFT order** through the ERP lifecycle. Invoicing still
> happens later in the ERP after approval/shipment -- the bot never
> bypasses the invoice/shipment rules.

## Architecture

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────┐
│  Telegram    │────▶│  bots/ project   │────▶│  Backend API │
│  / Bale Bot  │     │  (aiogram 3)     │     │  (FastAPI)   │
└─────────────┘     └─────────────────┘     └──────────────┘
                           │                       │
                           │  HTTP API calls        │
                           └───────────────────────┘
```

The `bots/` project is a **separate Python process** that communicates with the backend via REST API. This mirrors production deployment.

## Phone Verification Flow

```
User opens bot → /start → "Share Phone" button
    ↓
Contact received → POST /api/v1/bot/verify-phone
    ↓
Phone found + ACTIVE rep → JWT token (30 min) + representative_id
    ↓
Main menu with keyboard buttons:
  📦 موجودی انبار من → GET /bot/reps/{id}/inventory
  📊 گزارش فروش من → GET /bot/reps/{id}/reports
  🧾 صدور فاکتور جدید → order-creation conversation (see below)
```

## API Endpoints

### POST /api/v1/bot/verify-phone

Verify a phone number and receive a bot JWT token.

**Request:**
```json
{
  "phone_number": "+989123456789",
  "platform": "telegram",
  "chat_id": "123456789"
}
```

**Response (200):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 1800,
  "representative_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "representative_name": "Ali Ahmadi"
}
```

**Error (404):**
```json
{
  "detail": "Phone number '+989123456789' is not registered to any representative."
}
```

### GET /api/v1/bot/reps/{rep_id}/inventory

Get inventory for the representative's assigned warehouse.

**Headers:** `Authorization: Bearer <bot_token>`

**Response (200):**
```json
{
  "items": [
    {"sku": "SKU-0001", "name": "Steel Bolt 10mm", "balance": 150, "warehouse_code": "MAIN"}
  ],
  "warehouse_code": "MAIN"
}
```

### GET /api/v1/bot/reps/{rep_id}/reports

Get sales report for the representative.

**Headers:** `Authorization: Bearer <bot_token>`

**Response (200):**
```json
{
  "representative_name": "Ali Ahmadi",
  "period": "این ماه",
  "summaries": [
    {"label": "تعداد سفارشات", "value": 5},
    {"label": "درآمد", "value": "1,500,000"},
    {"label": "تعداد مشتریان", "value": 12},
    {"label": "دوره", "value": "این ماه"}
  ]
}
```

`period` query parameter accepts `today`, `this_week`, or `this_month` (default).

### GET /api/v1/bot/reps/{rep_id}/customers

List the customers the representative is allowed to sell to (ADR-007
representative scope -- never the full customer table).

**Headers:** `Authorization: Bearer <bot_token>`

**Response (200):**
```json
{
  "items": [
    {"id": "…uuid…", "code": "CUST-0001", "name": "شرکت ABC", "currency_id": "…uuid…"}
  ]
}
```

### GET /api/v1/bot/reps/{rep_id}/products

List the in-stock products of the representative's primary warehouse
(live balances computed from the immutable inventory ledger).

**Headers:** `Authorization: Bearer <bot_token>`

**Response (200):**
```json
{
  "items": [
    {"product_id": "…uuid…", "sku": "SKU-0001", "name": "Steel Bolt 10mm", "balance": 150}
  ],
  "warehouse_code": "MAIN"
}
```

### GET /api/v1/bot/reps/{rep_id}/price-preview

Resolve the ERP selling price for a product for a scoped customer. This is
read-only: the caller can **never** supply or override a price.

**Query params:** `customer_id`, `product_id`

**Response (200):**
```json
{
  "product_id": "…uuid…",
  "product_sku": "SKU-0001",
  "product_name": "Steel Bolt 10mm",
  "unit_price": 250000.0,
  "currency_id": "…uuid…",
  "price_list_id": "…uuid…",
  "price_type": "RETAIL"
}
```

**Error (422):** no price list assigned to the customer, or no currently
valid price for the product in that list.

### POST /api/v1/bot/reps/{rep_id}/orders

Create a **DRAFT order** whose prices are resolved by the ERP. The request
carries only the scoped customer and the lines (product + quantity) -- no
price, no representative id (the representative always comes from the JWT).

**Request:**
```json
{
  "customer_id": "…uuid…",
  "order_type": "LOCAL",
  "fulfillment_mode": "REP_LOCAL",
  "lines": [
    {"product_id": "…uuid…", "qty_ordered": 10}
  ]
}
```

**Response (200):**
```json
{
  "order_id": "…uuid…",
  "order_number": "ORD-20260904-ABC12345",
  "state": "DRAFT",
  "subtotal": 2500000.0,
  "grand_total": 2500000.0,
  "currency_id": "…uuid…",
  "lines": [
    {"product_id": "…uuid…", "product_sku": "SKU-0001", "product_name": "Steel Bolt 10mm",
     "qty_ordered": 10.0, "unit_price": 250000.0, "line_total": 2500000.0}
  ]
}
```

The order's `sales_channel` is `BOT_TELEGRAM`. Approval, reservation,
shipment and invoicing continue through the ERP exactly as for UI orders.

### POST /api/v1/bot/reps/{rep_id}/invoices

Create an invoice from a **shipped** order (legacy capability kept for the
ERP lifecycle: `SHIPPED order → DRAFT invoice`).

**Headers:** `Authorization: Bearer <bot_token>`

**Request:**
```json
{
  "order_number": "ORD-2026-0001"
}
```

**Response (200):**
```json
{
  "invoice_number": "INV-2026-0001",
  "order_number": "ORD-2026-0001",
  "status": "DRAFT",
  "grand_total": 500000,
  "message": "فاکتور با موفقیت صادر شد."
}
```

## Running the Bots

### Prerequisites

1. Backend server running (FastAPI on `:8000`) with migrations applied (`alembic upgrade head`)
2. Bot tokens configured — preferably via the ERP admin UI (**تنظیمات ← ربات‌ها و پیام‌رسان‌ها**), which stores them encrypted; `.env` (`TELEGRAM_BOT_TOKEN` / `BALE_BOT_TOKEN`) is a dev fallback

### Both platforms (recommended)

```bash
python -m bots.bot_service            # supervisor: real status reporting + restart-on-crash
python -m bots.bot_service --check    # show which platforms are configured
```

### A single platform

```bash
python -m bots.telegram_bot   # token: backend config, else TELEGRAM_BOT_TOKEN
python -m bots.bale_bot       # token: backend config, else BALE_BOT_TOKEN
```

### Testing with curl

```bash
# Verify phone
curl -X POST http://localhost:8000/api/v1/bot/verify-phone \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "+989123456789", "platform": "telegram", "chat_id": "123456789"}'

# Get inventory (replace TOKEN with the access_token from above)
curl http://localhost:8000/api/v1/bot/reps/{rep_id}/inventory \
  -H "Authorization: Bearer TOKEN"

# Get reports
curl http://localhost:8000/api/v1/bot/reps/{rep_id}/reports \
  -H "Authorization: Bearer TOKEN"

# List scoped customers
curl http://localhost:8000/api/v1/bot/reps/{rep_id}/customers \
  -H "Authorization: Bearer TOKEN"

# List warehouse products
curl http://localhost:8000/api/v1/bot/reps/{rep_id}/products \
  -H "Authorization: Bearer TOKEN"

# Resolve the ERP price for a customer+product
curl "http://localhost:8000/api/v1/bot/reps/{rep_id}/price-preview?customer_id={customer_id}&product_id={product_id}" \
  -H "Authorization: Bearer TOKEN"

# Create a DRAFT order (prices resolved by the ERP)
curl -X POST http://localhost:8000/api/v1/bot/reps/{rep_id}/orders \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer TOKEN" \
  -d '{"customer_id": "{customer_id}", "order_type": "LOCAL", "fulfillment_mode": "REP_LOCAL", "lines": [{"product_id": "{product_id}", "qty_ordered": 10}]}'

# Create an invoice from a shipped order (legacy lifecycle step)
curl -X POST http://localhost:8000/api/v1/bot/reps/{rep_id}/invoices \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer TOKEN" \
  -d '{"order_number": "ORD-2026-0001"}'
```

## Representative order-creation workflow

Selecting `🧾 صدور فاکتور جدید` starts a multi-step conversation (aiogram
FSM state machine, scoped per chat):

```
🧾 صدور فاکتور جدید
    ↓
Select customer        (GET /bot/reps/{id}/customers -- scoped only)
    ↓
Select product         (GET /bot/reps/{id}/products -- primary warehouse)
    ↓
Enter quantity         (validated > 0; current balance shown as a hint)
    ↓
Price + line amount    (GET /bot/reps/{id}/price-preview -- ERP price)
    ↓
➕ افزودن محصول  |  ✅ تأیید و ثبت  |  ❌ لغو
    ↓ (confirm)
POST /bot/reps/{id}/orders  →  DRAFT order created by order_service
    ↓
Order number + total + actual state (پیش‌نویس)
```

Rules enforced by this flow:

- **Customer scope:** the representative can only pick customers assigned
  to them (ADR-007). The representative id always comes from the verified
  bot JWT -- a caller can never read or order for another representative's
  customers.
- **Product scope:** only products available in the representative's
  primary warehouse inventory are offered.
- **Price comes from the ERP (BR-P1):** customer-specific price list →
  currently valid price entry. If no price can be resolved the bot shows
  a Persian message telling the representative to have the price list set
  up in the ERP first -- a manual price is never accepted.
- **Order, not invoice:** confirmation creates a DRAFT order via
  `order_service.create_order`. The bot never calculates totals itself and
  never creates an invoice directly; the ERP remains the source of truth
  for pricing, discounts, tax, currency, the state machine, audit and
  representative scope.
- **Quantity:** numeric and `> 0`; if the requested quantity exceeds the
  available balance the review shows a warning, but the ERP (reservation
  step) is the authority that enforces stock.

## Where administrators configure price lists

Admins configure price lists in the ERP UI:

1. Open **دفتر ← لیست قیمت‌ها** (`/office/price-lists`), permission
   `PRICE_LIST_MANAGE`.
2. **Create a price list** (name, price type, currency).
3. **Add prices** on the price list detail page: select a product and enter
   the selling price + effective date. Changing a price creates a **new
   version** (immutable `price_history`); previous versions are closed,
   never overwritten.
4. **Assign a price list to a customer** on the customer's detail page
   (اولویت/priority + effective window).

Effective price resolution follows BR-P1: the customer's assigned price
list (highest priority, currently active) → the product's currently valid
price entry. That is exactly the chain the bot's `price-preview` and
`order_service.create_order` use, so prices shown in Telegram always match
what the ERP will apply.

## File Structure

```
bots/
├── __init__.py             # Package init
├── config.py               # Token resolution (backend config, then env fallback)
├── shared.py               # Shared handlers, API clients, keyboards
├── bot_service.py          # Supervisor: starts/stops bots, live status, auto-restart
├── telegram_bot.py         # Telegram entry point
├── bale_bot.py             # Bale entry point
└── bale_bot_config.py      # Bale-specific API base URL

backend/app/api/v1/endpoints/
├── bot.py                  # Representative bot API (verify-phone, inventory, …)
├── bot_config.py           # Admin bot-settings API (config save/test/status)

backend/app/schemas/
├── bot.py                  # Representative request/response schemas
├── bot_config.py           # Admin bot-settings schemas (never carry the raw token)

services/
├── bot_phone_service.py    # Phone verification + JWT generation
├── bot_config_service.py   # Encrypted token storage + getMe connection tests

frontend/src/features/
├── bot-settings/           # Admin page: Telegram/Bale cards (token, save, test, enable)
└── representatives/        # Admin UI for managing reps (with phone numbers)
```

## Configuring bot tokens (admin)

Production tokens are configured in the ERP admin UI -- **not** by editing
`.env`. The tokens are stored Fernet-encrypted at rest in the `bot_config`
table and are **never** returned to the browser, written to logs, or echoed
in API errors. The admin UI only ever shows the last 4 characters as a hint.

The admin endpoints live under `/api/v1/bot-config` (RBAC permission
`BOT_MANAGE`); the bot processes fetch their own plaintext token at startup
from `GET /api/v1/bot-config/{platform}/token` using the shared
`X-Bot-Runtime-Secret` header.

### Telegram

1. Open Telegram and create a bot with [@BotFather](https://t.me/BotFather)
   (`/newbot`). BotFather returns an HTTP API token (`123456:ABC-DEF…`).
   Keep it secret.
2. Log in to the ERP as an admin (role with `BOT_MANAGE`).
3. Open **تنظیمات ← ربات‌ها و پیام‌رسان‌ها** (`/office/bot-settings`).
4. In the Telegram Bot card paste the token, click **ذخیره (Save)**.
   - The status line changes to "توکن تنظیم شده است" / shows `•••• <last-4>`;
     the raw token is never displayed again.
5. Click **تست اتصال (Test Connection)** — the backend calls the real
   Telegram Bot API `getMe` endpoint with the stored token.
6. On success the bot's real **name and username appear automatically**
   (derived from `getMe` — the admin never types them). On failure a useful
   error is shown without exposing the token.
7. Toggle **فعال (Enabled)** to start the platform.
8. Start the bot runtime (see below). The supervisor picks up the saved
   configuration automatically and live-restarts only that platform's
   process when the token changes or the platform is disabled.

### Bale

1. Obtain a Bale bot token from Bale's bot platform.
2. Repeat the same steps in the **Bale Bot** card (same ERP page).
3. Connection tests call Bale's own API base `https://tapi.bale.ai/bot…/getMe`
   (not Telegram's), and sessions are bound to `platform=bale` so a Bale
   identity is never mistaken for a Telegram one.

### Telegram network path (Sing-box)

When the Windows resolver returns an unusable address for `api.telegram.org`,
set `TELEGRAM_PROXY` to the existing local Sing-box HTTP or SOCKS inbound:

```dotenv
TELEGRAM_PROXY=http://127.0.0.1:<sing-box-http-port>
# or: TELEGRAM_PROXY=socks5://127.0.0.1:<sing-box-socks-port>
```

`bots/config.py` reads this variable and `bots/telegram_bot.py` passes it to
aiogram's `AiohttpSession(proxy=...)`. The setting affects only Telegram API
requests; it does not alter Windows DNS, pin a Telegram IP, disable TLS
verification, or affect backend/Bale requests. Leave it unset when the normal
OS/TUN path works. This repository cannot infer the host's Sing-box inbound
port, so it must match the listener configured on the machine.

### Token precedence

1. **Backend-managed config** — token saved via the ERP admin UI
   (encrypted `bot_config` row).
2. **Environment variable fallback (development only)** —
   `TELEGRAM_BOT_TOKEN` / `BALE_BOT_TOKEN`. The admin UI never writes back
   into `.env`.

### Runtime (how the configured token reaches the bot)

```
Admin UI save/test
      ↓
bot_config table (token encrypted at rest)
      ↓
GET /api/v1/bot-config/{platform}/token   (X-Bot-Runtime-Secret)
      ↓
bots/bot_service.py supervisor (or python -m bots.telegram_bot / bale_bot)
      ↓
real Telegram / Bale bot (aiogram polling)
```

The supervisor (`python -m bots.bot_service`) re-checks the stored
configuration every 15s: it starts a platform when it is enabled, stops it
when disabled, and **restarts only that platform's bot process** when the
token changes — no manual `.env` edit and no ERP restart required. Real
process state is reported back to the admin UI via the heartbeat endpoint,
so the status badge (تنظیم نشده / غیرفعال / در حال اجرا / خطا) is never faked.

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token — **dev fallback only** (admin UI is the primary path) | (unset) |
| `BALE_BOT_TOKEN` | Bale bot token — **dev fallback only** | (unset) |
| `TELEGRAM_PROXY` | Optional existing Sing-box HTTP(S)/SOCKS listener used only by aiogram for Telegram API traffic | (unset) |
| `BOT_API_BASE_URL` | Backend API base URL used by the bot processes | `http://localhost:8000` |
| `BOT_RUNTIME_SECRET` | Shared secret authorizing bot processes to fetch their token (`X-Bot-Runtime-Secret`) | `dev-bot-runtime-secret` (dev only) |

See `docs/BOT_SETUP.md` for the full runbook, and `.env.example` for
placeholders.

## Representative workflow

1. **Create representative:** Go to Office → Representatives → Add
   Representative — enter code, full name, national ID, and **phone number**.
2. **Representative opens the bot:** sends `/start`, then shares their phone
   via the "Share Phone" button.
3. **Phone verified:** backend binds the platform identity to the ACTIVE
   representative's registered phone and issues a short-lived bot JWT.
4. **Main menu appears:**
   - 📦 موجودی انبار من
   - 📊 گزارش فروش من
   - 🧾 صدور فاکتور جدید
5. **Representative uses the bot:** views inventory/reports; starts the
   order-creation conversation from `🧾 صدور فاکتور جدید` (customer →
   product → quantity → confirm). The ERP creates a DRAFT order; invoicing
   happens later in the ERP after approval/shipment. All data access is
   scoped (ADR-007) and RBAC-gated server-side (`BOT_QUERY` / `BOT_WRITE`).
