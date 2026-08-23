"""Request/response schemas for the Commission endpoints
(``/api/v1/commission-configs``).

Mirrors the structure established by ``app/schemas/invoices.py``.
Aligned field-for-field with ``database/models/commission_config.py`` (C1),
``commission_transaction.py`` (T23).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class CommissionOrderType(str, Enum):
    LOCAL = "LOCAL"
    DIRECT = "DIRECT"


class CommissionConfigCreateRequest(BaseModel):
    """Body for ``POST /commission-configs``."""

    rate: decimal.Decimal = Field(ge=0, le=100)
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None = None
    representative_id: uuid.UUID | None = None
    product_category_id: uuid.UUID | None = None
    order_type: CommissionOrderType


class CommissionConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    representative_id: uuid.UUID | None
    product_category_id: uuid.UUID | None
    order_type: str
    rate: decimal.Decimal
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None
    created_at: datetime.datetime


class CommissionConfigListResponse(BaseModel):
    items: list[CommissionConfigResponse]


class CommissionTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    representative_id: uuid.UUID
    order_id: uuid.UUID | None
    commission_config_id: uuid.UUID
    actor_user_id: uuid.UUID
    sequence_no: int
    signed_amount: decimal.Decimal
    state_event: str
    rate_applied: decimal.Decimal
    currency_id: uuid.UUID
    occurred_at: datetime.datetime


class CommissionCalculateRequest(BaseModel):
    """Body for ``POST /orders/{id}/commission``."""

    pass


__all__ = [
    "CommissionCalculateRequest",
    "CommissionConfigCreateRequest",
    "CommissionConfigListResponse",
    "CommissionConfigResponse",
    "CommissionOrderType",
    "CommissionTransactionResponse",
]
