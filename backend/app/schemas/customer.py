"""Request/response schemas for the Customer endpoints (``/api/v1/customers``).

REWRITTEN -- the previous version of this file modeled a *different*,
simpler ``Customer`` shape (integer ``id``, ``is_active: bool``, no
``code``/``currency_id``/``city_ref_id``) that does not match the actual
``database/models/customer.py`` (M8) ORM model. That model uses a UUID
primary key, a unique business ``code``, a required ``currency_id`` FK,
an ``ACTIVE``/``INACTIVE`` ``status`` string (not a bare boolean), and a
nullable ``city_ref_id`` FK. This version is aligned field-for-field with
that model -- see ``database/models/customer.py``'s own docstring for the
authoritative field-by-field rationale (ERD M8 / SRS E13).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class CustomerType(str, Enum):
    INDIVIDUAL = "INDIVIDUAL"
    CORPORATE = "CORPORATE"


class CustomerStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class CustomerCreateRequest(BaseModel):
    """Request body for ``POST /customers``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "CUST-0001",
                "name": "Acme Trading Co.",
                "type": "CORPORATE",
                "currency_id": "00000000-0000-0000-0000-000000000000",
                "billing_address": "Tehran, ...",
                "credit_limit_amount": "0",
                "tax_number": None,
                "city_ref_id": None,
            }
        }
    )

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    type: CustomerType
    currency_id: uuid.UUID
    city_ref_id: uuid.UUID | None = None
    billing_address: str | None = Field(default=None, max_length=255)
    credit_limit_amount: decimal.Decimal = Field(default=decimal.Decimal("0"), ge=0)
    tax_number: str | None = Field(default=None, max_length=40)


class CustomerUpdateRequest(BaseModel):
    """Request body for ``PATCH /customers/{customer_id}``. All fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    city_ref_id: uuid.UUID | None = None
    billing_address: str | None = Field(default=None, max_length=255)
    credit_limit_amount: decimal.Decimal | None = Field(default=None, ge=0)
    tax_number: str | None = Field(default=None, max_length=40)
    status: CustomerStatus | None = None


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    type: CustomerType
    city_ref_id: uuid.UUID | None
    billing_address: str | None
    credit_limit_amount: decimal.Decimal
    currency_id: uuid.UUID
    status: CustomerStatus
    tax_number: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CustomerListResponse(BaseModel):
    items: list[CustomerResponse]


# ---------------------------------------------------------------------------
# Customer Price List Assignment
# ---------------------------------------------------------------------------


class CustomerPriceListAssignRequest(BaseModel):
    """Request body for ``POST /customers/{id}/price-lists``.

    Assigns a price list to a customer with a time window and priority.
    Per ``02_SRS.md`` BR-P1, this is the mechanism that drives
    customer-specific pricing.
    """

    price_list_id: uuid.UUID
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None = None
    priority: int = Field(ge=1)


class CustomerPriceListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    price_list_id: uuid.UUID
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None
    priority: int
    created_at: datetime.datetime


class CustomerPriceListListResponse(BaseModel):
    items: list[CustomerPriceListResponse]


__all__ = [
    "CustomerCreateRequest",
    "CustomerListResponse",
    "CustomerPriceListAssignRequest",
    "CustomerPriceListListResponse",
    "CustomerPriceListResponse",
    "CustomerResponse",
    "CustomerStatus",
    "CustomerType",
    "CustomerUpdateRequest",
]
