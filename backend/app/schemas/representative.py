"""Request/response schemas for Representative endpoints."""

from __future__ import annotations

import datetime
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class RepresentativeStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    OFFBOARDED = "OFFBOARDED"


class RepresentativeCreateRequest(BaseModel):
    """Body for ``POST /representatives``."""

    code: str = Field(min_length=1, max_length=40)
    person_name: str = Field(min_length=1, max_length=160)
    national_id: str | None = Field(default=None, max_length=40)
    tax_id: str | None = Field(default=None, max_length=40)
    home_city_ref_id: uuid.UUID | None = None


class RepresentativeUpdateRequest(BaseModel):
    """Body for ``PATCH /representatives/{id}``. All fields optional."""

    person_name: str | None = Field(default=None, min_length=1, max_length=160)
    national_id: str | None = None
    tax_id: str | None = None
    home_city_ref_id: uuid.UUID | None = None
    status: RepresentativeStatus | None = None


class RepresentativeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    person_name: str
    national_id: str | None
    tax_id: str | None
    home_city_ref_id: uuid.UUID | None
    status: RepresentativeStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime


class RepresentativeListResponse(BaseModel):
    items: list[RepresentativeResponse]


__all__ = [
    "RepresentativeCreateRequest",
    "RepresentativeListResponse",
    "RepresentativeResponse",
    "RepresentativeStatus",
    "RepresentativeUpdateRequest",
]
