"""Request/response schemas for the Stock Transfer endpoints
(``/api/v1/transfers``).

Mirrors the structure established by ``app/schemas/invoices.py``.
Aligned field-for-field with ``database/models/stock_transfer.py`` (T4),
``transfer_line.py`` (T5), and ``transfer_history.py`` (T6).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TransferState(str, Enum):
    DRAFT = "DRAFT"
    DISPATCHED = "DISPATCHED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


class TransferLineCreateRequest(BaseModel):
    """One line of ``POST /transfers``'s request body."""

    product_id: uuid.UUID
    qty_requested: decimal.Decimal = Field(gt=0)
    unit_cost: decimal.Decimal = Field(ge=0)
    lot_id: uuid.UUID | None = None


class TransferCreateRequest(BaseModel):
    """Body for ``POST /transfers``."""

    source_warehouse_id: uuid.UUID
    destination_warehouse_id: uuid.UUID
    lines: list[TransferLineCreateRequest] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=2000)


class TransferTransitionRequest(BaseModel):
    """Generic body for simple one-step transition endpoints (dispatch, receive, cancel)."""

    note: str | None = Field(default=None, max_length=2000)


class TransferLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stock_transfer_id: uuid.UUID
    product_id: uuid.UUID
    lot_id: uuid.UUID | None
    qty_requested: decimal.Decimal
    qty_dispatched: decimal.Decimal
    qty_received: decimal.Decimal
    unit_cost: decimal.Decimal
    qty_variance: decimal.Decimal


class TransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    transfer_number: str
    source_warehouse_id: uuid.UUID
    destination_warehouse_id: uuid.UUID
    state: TransferState
    requested_by: uuid.UUID
    approved_by: uuid.UUID | None
    requested_at: datetime.datetime
    approved_at: datetime.datetime | None
    dispatched_at: datetime.datetime | None
    received_at: datetime.datetime | None
    ownership_mode_snapshot: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    lines: list[TransferLineResponse] = []


class TransferListResponse(BaseModel):
    items: list[TransferResponse]


class TransferLineListResponse(BaseModel):
    items: list[TransferLineResponse]


class TransferHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    stock_transfer_id: uuid.UUID
    actor_user_id: uuid.UUID
    from_state: TransferState
    to_state: TransferState
    event_at: datetime.datetime
    note: str | None


class TransferHistoryListResponse(BaseModel):
    items: list[TransferHistoryResponse]


__all__ = [
    "TransferCreateRequest",
    "TransferHistoryListResponse",
    "TransferHistoryResponse",
    "TransferLineCreateRequest",
    "TransferLineListResponse",
    "TransferLineResponse",
    "TransferListResponse",
    "TransferResponse",
    "TransferState",
    "TransferTransitionRequest",
]
