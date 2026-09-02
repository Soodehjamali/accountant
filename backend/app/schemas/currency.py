"""Minimal currency schemas for the default-currency endpoint.

This is NOT a full currency-management feature — it exposes only the
read-only ``GET /currencies/default`` endpoint needed by the frontend
to obtain the real IRR currency UUID for inventory transactions.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class CurrencyResponse(BaseModel):
    """Read-only representation of a currency row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    symbol: str
    decimals: int
    is_base: bool


__all__ = ["CurrencyResponse"]
