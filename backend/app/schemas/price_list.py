"""Request/response schemas for PriceList and PriceHistory endpoints."""

from __future__ import annotations

import datetime
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PriceType(str, Enum):
    RETAIL = "RETAIL"
    REP = "REP"
    WHOLESALE = "WHOLESALE"
    EXPORT = "EXPORT"
    PROMO = "PROMO"


# ---------------------------------------------------------------------------
# Price List
# ---------------------------------------------------------------------------


class PriceListCreateRequest(BaseModel):
    """Body for ``POST /price-lists``."""

    name: str = Field(min_length=1, max_length=160)
    price_type: PriceType
    currency_id: uuid.UUID
    owner_scope: str = Field(min_length=1, max_length=255)


class PriceListUpdateRequest(BaseModel):
    """Body for ``PATCH /price-lists/{id}``.  All fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    owner_scope: str | None = Field(default=None, max_length=255)


class PriceListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price_type: PriceType
    currency_id: uuid.UUID
    owner_scope: str
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PriceListListResponse(BaseModel):
    items: list[PriceListResponse]


# ---------------------------------------------------------------------------
# Price History (price entries)
# ---------------------------------------------------------------------------


class PriceEntryCreateRequest(BaseModel):
    """Body for ``POST /price-lists/{id}/items``."""

    product_id: uuid.UUID
    unit_price: float = Field(gt=0)
    effective_from: datetime.datetime
    reason: str | None = Field(default=None, max_length=255)
    is_promo: bool = False
    promo_valid_from: datetime.datetime | None = None
    promo_valid_to: datetime.datetime | None = None


class PriceEntryUpdateRequest(BaseModel):
    """Body for ``PATCH /price-lists/{id}/items/{item_id}``.

    PriceHistory is append-only — updating a price entry means
    creating a new version.  This schema carries the new price
    details.
    """

    unit_price: float = Field(gt=0)
    effective_from: datetime.datetime
    reason: str | None = Field(default=None, max_length=255)
    is_promo: bool = False
    promo_valid_from: datetime.datetime | None = None
    promo_valid_to: datetime.datetime | None = None


class PriceEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    price_list_id: uuid.UUID
    currency_id: uuid.UUID
    price_type: PriceType
    unit_price: float
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None
    is_promo: bool
    promo_valid_from: datetime.datetime | None
    promo_valid_to: datetime.datetime | None
    reason: str | None
    created_at: datetime.datetime


class PriceEntryListResponse(BaseModel):
    items: list[PriceEntryResponse]


__all__ = [
    "PriceEntryCreateRequest",
    "PriceEntryListResponse",
    "PriceEntryResponse",
    "PriceEntryUpdateRequest",
    "PriceListCreateRequest",
    "PriceListListResponse",
    "PriceListResponse",
    "PriceListUpdateRequest",
    "PriceType",
]
