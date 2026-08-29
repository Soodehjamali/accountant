"""Request/response schemas for Warehouse and WarehouseAssignment endpoints."""

from __future__ import annotations

import datetime
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class WarehouseType(str, Enum):
    FACTORY = "FACTORY"
    REPRESENTATIVE = "REPRESENTATIVE"


class OwnershipMode(str, Enum):
    OWNED = "OWNED"
    CONSIGNMENT = "CONSIGNMENT"


class WarehouseStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class WarehouseCreateRequest(BaseModel):
    """Body for ``POST /warehouses``."""

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    type: WarehouseType
    ownership_mode: OwnershipMode
    address: str | None = Field(default=None, max_length=255)
    city_ref_id: uuid.UUID | None = None
    latitude: float | None = None
    longitude: float | None = None
    responsible_user_id: uuid.UUID | None = None


class WarehouseUpdateRequest(BaseModel):
    """Body for ``PATCH /warehouses/{id}``. All fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    address: str | None = None
    city_ref_id: uuid.UUID | None = None
    latitude: float | None = None
    longitude: float | None = None
    responsible_user_id: uuid.UUID | None = None
    status: WarehouseStatus | None = None


class WarehouseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    type: WarehouseType
    ownership_mode: OwnershipMode
    address: str | None
    city_ref_id: uuid.UUID | None
    latitude: float | None
    longitude: float | None
    status: WarehouseStatus
    responsible_user_id: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class WarehouseListResponse(BaseModel):
    items: list[WarehouseResponse]


class WarehouseAssignmentCreateRequest(BaseModel):
    """Body for ``POST /warehouses/{id}/assignments``."""

    representative_id: uuid.UUID
    is_primary: bool = False
    effective_from: datetime.datetime | None = None
    effective_to: datetime.datetime | None = None


class WarehouseAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    representative_id: uuid.UUID
    warehouse_id: uuid.UUID
    is_primary: bool
    effective_from: datetime.datetime
    effective_to: datetime.datetime | None


class WarehouseAssignmentListResponse(BaseModel):
    items: list[WarehouseAssignmentResponse]


__all__ = [
    "OwnershipMode",
    "WarehouseAssignmentCreateRequest",
    "WarehouseAssignmentListResponse",
    "WarehouseAssignmentResponse",
    "WarehouseCreateRequest",
    "WarehouseListResponse",
    "WarehouseResponse",
    "WarehouseStatus",
    "WarehouseType",
    "WarehouseUpdateRequest",
]
