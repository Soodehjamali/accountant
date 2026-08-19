"""Request/response schemas for the inventory ledger endpoints
(``/api/v1/inventory``).

Kept independent of the SQLAlchemy ``InventoryTransaction`` ORM model,
mirroring the pattern already established by ``app/schemas/product.py``.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from pydantic import BaseModel, ConfigDict, Field


class PostTransactionRequest(BaseModel):
    """Request body for ``POST /inventory/transactions``.

    Mirrors ``services.inventory_service.post_transaction``'s own
    parameters. ``signed_quantity``'s sign must match the posted
    ``movement_type_code``'s sign convention (validated in the service
    layer, not here -- see that module's docstring on why).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "product_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "warehouse_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "movement_type_code": "RECEIPT_FROM_PRODUCTION",
                "signed_quantity": "100.0000",
                "unit_cost": "12.500000",
                "currency_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            }
        }
    )

    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    movement_type_code: str = Field(min_length=1, max_length=40)
    signed_quantity: decimal.Decimal
    unit_cost: decimal.Decimal
    currency_id: uuid.UUID
    lot_id: uuid.UUID | None = None
    reason_code_id: uuid.UUID | None = None
    reference_type: str | None = Field(default=None, max_length=40)
    reference_id: uuid.UUID | None = None


class ReverseTransactionRequest(BaseModel):
    """Request body for ``POST /inventory/transactions/{id}/reverse``."""

    reason_code_id: uuid.UUID | None = None


class TransactionResponse(BaseModel):
    """Response body for a single ``inventory_transaction`` row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    lot_id: uuid.UUID | None
    warehouse_id: uuid.UUID
    movement_type_id: uuid.UUID
    sequence_no: int
    signed_quantity: decimal.Decimal
    unit_cost: decimal.Decimal
    currency_id: uuid.UUID
    occurred_at: datetime.datetime
    reference_type: str | None
    reference_id: uuid.UUID | None
    reversal_of_id: uuid.UUID | None
    is_reversed: bool
    row_hash: str
    prev_hash: str | None


class BalanceResponse(BaseModel):
    """Response body for ``GET /inventory/balance``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "warehouse_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "product_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "lot_id": None,
                "balance": "100.0000",
            }
        }
    )

    warehouse_id: uuid.UUID
    product_id: uuid.UUID
    lot_id: uuid.UUID | None
    balance: decimal.Decimal


__all__ = [
    "BalanceResponse",
    "PostTransactionRequest",
    "ReverseTransactionRequest",
    "TransactionResponse",
]
