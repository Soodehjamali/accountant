"""Request/response schemas for the Invoice endpoints (``/api/v1/invoices``).

Mirrors the structure established by ``app/schemas/orders.py``.  Aligned
field-for-field with ``database/models/invoice.py`` (T17),
``invoice_line.py`` (T18), and ``invoice_history.py`` (H4).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class InvoiceState(str, Enum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    CLOSED_CORRECTED = "CLOSED_CORRECTED"
    VOID = "VOID"


class InvoiceCreateFromOrderRequest(BaseModel):
    """Body for ``POST /invoices/from-order``."""

    order_id: uuid.UUID
    due_days: int = Field(default=30, ge=1, le=365)
    note: str | None = Field(default=None, max_length=2000)


class InvoiceTransitionRequest(BaseModel):
    """Generic body for simple one-step transition endpoints (issue, void)."""

    note: str | None = Field(default=None, max_length=2000)


class InvoicePaymentRequest(BaseModel):
    """Body for ``POST /invoices/{id}/pay``."""

    amount: decimal.Decimal = Field(gt=0)
    note: str | None = Field(default=None, max_length=2000)


class InvoiceLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    order_line_id: uuid.UUID | None
    product_id: uuid.UUID | None
    description: str
    qty: decimal.Decimal
    unit_price: decimal.Decimal
    tax_rate: decimal.Decimal
    tax_amount: decimal.Decimal
    discount_value: decimal.Decimal
    line_total: decimal.Decimal


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID
    currency_id: uuid.UUID
    state: InvoiceState
    subtotal: decimal.Decimal
    tax_total: decimal.Decimal
    discount_total: decimal.Decimal
    grand_total: decimal.Decimal
    amount_paid: decimal.Decimal
    balance_due: decimal.Decimal
    issued_at: datetime.datetime | None
    due_at: datetime.datetime | None
    closed_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    lines: list[InvoiceLineResponse] = []


class InvoiceListResponse(BaseModel):
    items: list[InvoiceResponse]


class InvoiceLineListResponse(BaseModel):
    items: list[InvoiceLineResponse]


class InvoiceHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    actor_user_id: uuid.UUID
    from_state: InvoiceState
    to_state: InvoiceState
    event_at: datetime.datetime
    note: str | None


class InvoiceHistoryListResponse(BaseModel):
    items: list[InvoiceHistoryResponse]


__all__ = [
    "InvoiceCreateFromOrderRequest",
    "InvoiceHistoryListResponse",
    "InvoiceHistoryResponse",
    "InvoiceLineListResponse",
    "InvoiceLineResponse",
    "InvoiceListResponse",
    "InvoicePaymentRequest",
    "InvoiceResponse",
    "InvoiceState",
    "InvoiceTransitionRequest",
]
