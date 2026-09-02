"""Request/response schemas for Customer Return endpoints.

Mirrors the structure established by other domain schemas in this codebase.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ReturnType(str, Enum):
    CUSTOMER_RETURN = "CUSTOMER_RETURN"
    REP_RETURN_TO_FACTORY = "REP_RETURN_TO_FACTORY"
    DAMAGED_RETURN = "DAMAGED_RETURN"


class ReturnState(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    RECEIVED = "RECEIVED"
    INSPECTED = "INSPECTED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"


class ReturnLineCreateRequest(BaseModel):
    """One line item in a return creation request."""

    product_id: uuid.UUID
    order_line_id: uuid.UUID | None = None
    qty_returned: decimal.Decimal = Field(gt=0)
    unit_refund_amount: decimal.Decimal = Field(ge=0, default=0)


class CustomerReturnCreateRequest(BaseModel):
    """Body for POST /customer-returns."""

    order_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    representative_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID
    reason_code_id: uuid.UUID
    return_type: ReturnType
    note: str | None = Field(default=None, max_length=2000)
    lines: list[ReturnLineCreateRequest] = Field(min_length=1)


class ReturnLineResponse(BaseModel):
    """Response for a single return line."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_return_id: uuid.UUID
    order_line_id: uuid.UUID | None
    product_id: uuid.UUID
    lot_id: uuid.UUID | None
    qty_returned: decimal.Decimal
    condition: str | None
    disposition: str | None
    unit_refund_amount: decimal.Decimal


class CustomerReturnResponse(BaseModel):
    """Response for a single customer return."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    return_number: str
    order_id: uuid.UUID | None
    customer_id: uuid.UUID | None
    representative_id: uuid.UUID | None
    warehouse_id: uuid.UUID
    initiated_by: uuid.UUID
    reason_code_id: uuid.UUID
    return_type: str
    state: str
    requested_at: datetime.datetime
    received_at: datetime.datetime | None
    closed_at: datetime.datetime | None
    created_at: datetime.datetime
    lines: list[ReturnLineResponse] = []


class CustomerReturnListResponse(BaseModel):
    """Response for listing customer returns."""

    items: list[CustomerReturnResponse]


class ReturnTransitionRequest(BaseModel):
    """Body for POST /customer-returns/{id}/approve|receive|inspect|close."""

    note: str | None = Field(default=None, max_length=2000)


__all__ = [
    "CustomerReturnCreateRequest",
    "CustomerReturnListResponse",
    "CustomerReturnResponse",
    "ReturnLineCreateRequest",
    "ReturnLineResponse",
    "ReturnState",
    "ReturnType",
    "ReturnTransitionRequest",
]
