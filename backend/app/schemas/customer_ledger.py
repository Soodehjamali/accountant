"""Request/response schemas for the Customer Ledger endpoints
(``/api/v1/customers/{id}/ledger``, ``/api/v1/customers/{id}/balance``).

Mirrors the structure established by ``app/schemas/audit_log.py``.
Aligned field-for-field with ``database/models/customer_ledger_entry.py``
(T22) and ``database/models/customer_ledger.py`` (M13).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class LedgerEntryType(str, Enum):
    INVOICE_ISSUED = "INVOICE_ISSUED"
    PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
    CREDIT_NOTE_APPLIED = "CREDIT_NOTE_APPLIED"
    WRITE_OFF = "WRITE_OFF"


class CustomerLedgerEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_ledger_id: uuid.UUID
    actor_user_id: uuid.UUID
    reference_type: str
    reference_id: uuid.UUID
    sequence_no: int
    signed_amount: decimal.Decimal
    currency_id: uuid.UUID
    occurred_at: datetime.datetime
    entry_type: LedgerEntryType
    row_hash: str
    prev_hash: str | None
    created_at: datetime.datetime


class CustomerLedgerEntryListResponse(BaseModel):
    items: list[CustomerLedgerEntryResponse]


class CustomerBalanceResponse(BaseModel):
    """Live balance computed from the entry log (not the cached projection)."""

    customer_id: uuid.UUID
    balance: decimal.Decimal
    computed_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class CustomerLedgerReconcileResponse(BaseModel):
    """Response from the reconciliation endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    current_balance: decimal.Decimal
    last_entry_seq: int
    last_reconciled_at: datetime.datetime


__all__ = [
    "CustomerBalanceResponse",
    "CustomerLedgerEntryListResponse",
    "CustomerLedgerEntryResponse",
    "CustomerLedgerReconcileResponse",
    "LedgerEntryType",
]
