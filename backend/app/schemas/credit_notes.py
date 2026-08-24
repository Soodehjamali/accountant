"""Request/response schemas for the Credit Note endpoints
(``/api/v1/credit-notes``).

Mirrors the structure established by ``app/schemas/invoices.py``.
Aligned field-for-field with ``database/models/credit_note.py`` (T20),
``credit_note_line.py`` (T21).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class CreditNoteState(str, Enum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    APPLIED = "APPLIED"
    VOID = "VOID"


class CreditNoteLineCreateRequest(BaseModel):
    """One line item for ``POST /credit-notes``."""

    invoice_line_id: uuid.UUID | None = None
    description: str = Field(max_length=255)
    qty: decimal.Decimal = Field(gt=0)
    unit_price: decimal.Decimal = Field(gt=0)


class CreditNoteCreateRequest(BaseModel):
    """Body for ``POST /credit-notes``."""

    invoice_id: uuid.UUID
    reason_code_id: uuid.UUID
    lines: list[CreditNoteLineCreateRequest] = Field(min_length=1)
    reference_type: str | None = Field(default=None, max_length=40)
    reference_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


class CreditNoteLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    credit_note_id: uuid.UUID
    invoice_line_id: uuid.UUID | None
    description: str
    qty: decimal.Decimal
    unit_price: decimal.Decimal
    line_total: decimal.Decimal


class CreditNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    credit_note_number: str
    invoice_id: uuid.UUID
    customer_id: uuid.UUID
    issued_by: uuid.UUID
    reason_code_id: uuid.UUID
    reference_type: str | None
    reference_id: uuid.UUID | None
    total_amount: decimal.Decimal
    state: CreditNoteState
    issued_at: datetime.datetime | None
    created_at: datetime.datetime

    # Populated by the endpoint, not the ORM directly.
    lines: list[CreditNoteLineResponse] = []


class CreditNoteListResponse(BaseModel):
    items: list[CreditNoteResponse]


class CreditNoteTransitionRequest(BaseModel):
    """Generic body for simple one-step transition endpoints (issue, apply, void)."""

    note: str | None = Field(default=None, max_length=2000)


__all__ = [
    "CreditNoteCreateRequest",
    "CreditNoteLineCreateRequest",
    "CreditNoteLineResponse",
    "CreditNoteListResponse",
    "CreditNoteResponse",
    "CreditNoteState",
    "CreditNoteTransitionRequest",
]
