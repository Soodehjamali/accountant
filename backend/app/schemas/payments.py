"""Request/response schemas for the Payment endpoints (``/api/v1/payments``).

Mirrors the structure established by ``app/schemas/invoices.py``.
Aligned field-for-field with ``database/models/payment.py`` (T19),
``payment_allocation.py`` (J2).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PaymentMethod(str, Enum):
    CASH = "CASH"
    BANK_TRANSFER = "BANK_TRANSFER"
    CHEQUE = "CHEQUE"
    CARD = "CARD"
    MOBILE_WALLET = "MOBILE_WALLET"


class PaymentAllocationCreateRequest(BaseModel):
    """One allocation entry of ``POST /payments``."""

    invoice_id: uuid.UUID
    allocated_amount: decimal.Decimal = Field(gt=0)


class PaymentCreateRequest(BaseModel):
    """Body for ``POST /payments``."""

    customer_id: uuid.UUID
    currency_id: uuid.UUID
    amount: decimal.Decimal = Field(gt=0)
    method: PaymentMethod
    reference: str | None = Field(default=None, max_length=120)
    received_at: datetime.datetime | None = None
    allocations: list[PaymentAllocationCreateRequest] = Field(min_length=1)


class PaymentAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payment_id: uuid.UUID
    invoice_id: uuid.UUID
    allocated_amount: decimal.Decimal
    allocated_at: datetime.datetime


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payment_number: str
    customer_id: uuid.UUID
    currency_id: uuid.UUID
    received_by: uuid.UUID
    amount: decimal.Decimal
    method: str
    reference: str | None
    received_at: datetime.datetime
    unallocated_amount: decimal.Decimal
    created_at: datetime.datetime
    allocations: list[PaymentAllocationResponse] = []


class PaymentListResponse(BaseModel):
    items: list[PaymentResponse]


class PaymentAllocationListResponse(BaseModel):
    items: list[PaymentAllocationResponse]


__all__ = [
    "PaymentAllocationCreateRequest",
    "PaymentAllocationListResponse",
    "PaymentAllocationResponse",
    "PaymentCreateRequest",
    "PaymentListResponse",
    "PaymentMethod",
    "PaymentResponse",
]
