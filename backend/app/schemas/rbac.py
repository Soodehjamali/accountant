"""Request/response schemas for the RBAC endpoints (``/api/v1/rbac``)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class RoleCreateRequest(BaseModel):
    """Request body for ``POST /rbac/roles``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "WAREHOUSE_CLERK",
                "name": "Warehouse Clerk",
                "description": "Can post inventory receipts and view balances.",
            }
        }
    )

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=255)


class PermissionCreateRequest(BaseModel):
    """Request body for ``POST /rbac/permissions``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "INVENTORY_POST",
                "name": "Post inventory transactions",
                "resource": "inventory",
                "action": "post",
            }
        }
    )

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    resource: str = Field(min_length=1, max_length=40)
    action: str = Field(min_length=1, max_length=40)


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None


class PermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    resource: str
    action: str


class RoleListResponse(BaseModel):
    items: list[RoleResponse]


class PermissionListResponse(BaseModel):
    items: list[PermissionResponse]


class AssignRoleRequest(BaseModel):
    """Request body for ``POST /rbac/users/{user_id}/roles``."""

    role_code: str = Field(min_length=1, max_length=40)


class MyPermissionsResponse(BaseModel):
    """Response body for ``GET /rbac/me/permissions``."""

    permission_codes: list[str]


__all__ = [
    "AssignRoleRequest",
    "MyPermissionsResponse",
    "PermissionCreateRequest",
    "PermissionListResponse",
    "PermissionResponse",
    "RoleCreateRequest",
    "RoleListResponse",
    "RoleResponse",
]
