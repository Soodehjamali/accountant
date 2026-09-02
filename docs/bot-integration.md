# Bot Integration Documentation

## Overview

The Enterprise ERP system supports Telegram and Bale bots, allowing sales representatives to:

1. **Authenticate** via phone number verification
2. **View inventory** (stock levels in their assigned warehouse)
3. **View reports** (sales summary, customer count, monthly revenue)
4. **Create invoices** from shipped orders

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
Phone found + ACTIVE rep → JWT token (30 min)
    ↓
Main menu with keyboard buttons:
  📦 موجودی انبار من → GET /bot/reps/{id}/inventory
  📊 گزارش فروش من → GET /bot/reps/{id}/reports
  🧾 صدور فاکتور جدید → POST /bot/reps/{id}/invoices
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
  "period": "2026-09",
  "summaries": [
    {"label": "تعداد سفارشات", "value": 5},
    {"label": "درآمد ماهانه", "value": "1,500,000"},
    {"label": "تعداد مشتریان", "value": 12}
  ]
}
```

### POST /api/v1/bot/reps/{rep_id}/invoices

Create an invoice from a shipped order.

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

1. Backend server running: `cd backend && uvicorn app.main:app --reload --app-dir backend`
2. Bot tokens configured in `.env`

### Telegram Bot

```bash
# Set the token
export TELEGRAM_BOT_TOKEN="your_token_here"

# Run the bot
python -m bots.telegram_bot
```

### Bale Bot

```bash
# Set the token
export BALE_BOT_TOKEN="your_token_here"

# Run the bot
python -m bots.bale_bot
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

# Create invoice
curl -X POST http://localhost:8000/api/v1/bot/reps/{rep_id}/invoices \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer TOKEN" \
  -d '{"order_number": "ORD-2026-0001"}'
```

## File Structure

```
bots/
├── __init__.py           # Package init
├── config.py             # Shared configuration (env vars)
├── shared.py             # Shared handlers, API clients, keyboards
├── telegram_bot.py       # Telegram entry point
├── bale_bot.py           # Bale entry point
└── bale_bot_config.py    # Bale-specific API base URL

backend/app/
├── api/v1/endpoints/bot.py    # Bot API endpoints
├── dependencies/bot_auth.py   # JWT validation for bot tokens
├── schemas/bot.py             # Request/response schemas
└── api/v1/router.py           # Updated to include bot router

services/
├── bot_phone_service.py       # Phone verification + JWT generation

frontend/src/features/representatives/
├── RepresentativeListPage.tsx  # Admin UI for managing reps
└── RepresentativeCreatePage.tsx # Create form (with phone number)
```

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather | (required for Telegram bot) |
| `BALE_BOT_TOKEN` | Bale bot token | (required for Bale bot) |
| `BOT_API_BASE_URL` | Backend API base URL | `http://localhost:8000` |

## Admin Workflow

1. **Create representative:** Go to Office → Representatives → Add Representative
   - Enter code, full name, national ID, and **phone number**
2. **Representative opens bot:** Shares phone number via the bot's "Share Phone" button
3. **Phone verified:** Bot authenticates and shows the main menu
4. **Representative uses bot:** Views inventory, reports, creates invoices
