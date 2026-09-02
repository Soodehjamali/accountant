"""Request/response schemas for the bot endpoints (``/api/v1/bot/...``).

Kept independent of SQLAlchemy ORM models (per ``app/schemas/__init__.py``
convention).  ``BotVerifyPhoneRequest`` is the entry point for the phone
verification flow; the response schemas cover the bot data endpoints.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Phone verification
# ---------------------------------------------------------------------------


class BotVerifyPhoneRequest(BaseModel):
    """Request body for ``POST /bot/verify-phone``.

    The bot sends this when a representative shares their phone number
    via the Telegram/Bale contact-sharing button.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "phone_number": "+989123456789",
                "platform": "telegram",
                "chat_id": "123456789",
            }
        }
    )

    phone_number: str = Field(
        ..., min_length=5, max_length=20, description="Phone number in E.164 or local format."
    )
    platform: str = Field(
        ..., description="Platform code: 'telegram' or 'bale'.", pattern="^(telegram|bale)$"
    )
    chat_id: str = Field(
        ..., min_length=1, max_length=50, description="Platform-specific chat identifier."
    )


class BotVerifyPhoneResponse(BaseModel):
    """Response body for a successful ``POST /bot/verify-phone``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 1800,
                "representative_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "representative_name": "Ali Ahmadi",
            }
        }
    )

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    representative_id: uuid.UUID
    representative_name: str


# ---------------------------------------------------------------------------
# Bot data endpoints
# ---------------------------------------------------------------------------


class BotInventoryItem(BaseModel):
    """A single inventory balance row for bot display."""

    model_config = ConfigDict(from_attributes=True)

    sku: str
    name: str
    balance: int
    warehouse_code: str


class BotInventoryResponse(BaseModel):
    """Response body for ``GET /bot/reps/{rep_id}/inventory``."""

    items: list[BotInventoryItem]
    warehouse_code: str


class BotReportSummary(BaseModel):
    """A summary row for the representative's report."""

    model_config = ConfigDict(from_attributes=True)

    label: str
    value: str | int | float


class BotReportResponse(BaseModel):
    """Response body for ``GET /bot/reps/{rep_id}/reports``."""

    representative_name: str
    period: str
    summaries: list[BotReportSummary]


class BotInvoiceLineItem(BaseModel):
    """A single line item for invoice creation."""

    product_sku: str
    quantity: int
    unit_price: float


class BotInvoiceCreateRequest(BaseModel):
    """Request body for ``POST /bot/reps/{rep_id}/invoices``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "order_number": "ORD-2026-0001",
                "items": [
                    {"product_sku": "SKU-0001", "quantity": 10, "unit_price": 50000.0}
                ],
            }
        }
    )

    order_number: str = Field(..., min_length=1, description="Order number to invoice.")
    items: list[BotInvoiceLineItem] = Field(
        default_factory=list,
        description="Line items (optional -- if empty, invoicing the entire order).",
    )


class BotInvoiceResponse(BaseModel):
    """Response body for ``POST /bot/reps/{rep_id}/invoices``."""

    invoice_number: str
    order_number: str
    status: str
    grand_total: float
    message: str


# ---------------------------------------------------------------------------
# Generic error
# ---------------------------------------------------------------------------


class BotErrorResponse(BaseModel):
    """Error response body for bot endpoints."""

    detail: str


__all__ = [
    "BotErrorResponse",
    "BotInvoiceCreateRequest",
    "BotInvoiceLineItem",
    "BotInvoiceResponse",
    "BotInventoryItem",
    "BotInventoryResponse",
    "BotReportResponse",
    "BotReportSummary",
    "BotVerifyPhoneRequest",
    "BotVerifyPhoneResponse",
]
