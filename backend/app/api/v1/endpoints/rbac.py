"""RBAC endpoints: role/permission definitions, grants, and role assignment.

Thin HTTP wrappers around ``services.rbac_service`` -- business rules
live there, per this project's layering rule. Every mutating endpoint is
gated behind holding the ``RBAC_MANAGE`` permission (see
``services/bootstrap_service.py``'s ``ensure_rbac_bootstrap`` for how the
first account gets it); ``GET /rbac/me/permissions`` only requires being
authenticated, since it answers "what can *I* do", not an administrative
question.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.rbac import (
    AssignRoleRequest,
    MyPermissionsResponse,
    PermissionCreateRequest,
    PermissionListResponse,
    PermissionResponse,
    RoleCreateRequest,
    RoleListResponse,
    RoleResponse,
)
from database.models.app_user import AppUser
from services import rbac_service
from services.bootstrap_service import RBAC_MANAGE_PERMISSION_CODE

router = APIRouter(prefix="/rbac", tags=["rbac"])

_require_rbac_manage = require_permission(RBAC_MANAGE_PERMISSION_CODE)


@router.post(
    "/roles",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a role",
)
def create_role(
    body: RoleCreateRequest,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_rbac_manage),
) -> RoleResponse:
    try:
        role = rbac_service.create_role(
            db, code=body.code, name=body.name, description=body.description
        )
    except rbac_service.DuplicateRoleCodeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(role)
    return role


@router.get("/roles", response_model=RoleListResponse, summary="List roles")
def list_roles(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> RoleListResponse:
    return RoleListResponse(items=list(rbac_service.list_roles(db)))


@router.post(
    "/permissions",
    response_model=PermissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a permission",
)
def create_permission(
    body: PermissionCreateRequest,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_rbac_manage),
) -> PermissionResponse:
    try:
        permission = rbac_service.create_permission(
            db,
            code=body.code,
            name=body.name,
            resource=body.resource,
            action=body.action,
        )
    except rbac_service.DuplicatePermissionCodeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(permission)
    return permission


@router.get(
    "/permissions", response_model=PermissionListResponse, summary="List permissions"
)
def list_permissions(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> PermissionListResponse:
    return PermissionListResponse(items=list(rbac_service.list_permissions(db)))


@router.post(
    "/roles/{role_code}/permissions/{permission_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Grant a permission to a role",
)
def grant_permission_to_role(
    role_code: str,
    permission_code: str,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_rbac_manage),
) -> None:
    try:
        rbac_service.grant_permission_to_role(
            db, role_code=role_code, permission_code=permission_code
        )
    except (rbac_service.RoleNotFoundError, rbac_service.PermissionNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()


@router.post(
    "/users/{user_id}/roles",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign a role to a user",
)
def assign_role(
    user_id: uuid.UUID,
    body: AssignRoleRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_rbac_manage),
) -> None:
    try:
        rbac_service.assign_role(
            db,
            user_id=user_id,
            role_code=body.role_code,
            assigned_by=current_user.id,
        )
    except (rbac_service.UserNotFoundError, rbac_service.RoleNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()


@router.delete(
    "/users/{user_id}/roles/{role_code}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a role from a user",
)
def revoke_role(
    user_id: uuid.UUID,
    role_code: str,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_rbac_manage),
) -> None:
    try:
        rbac_service.revoke_role(db, user_id=user_id, role_code=role_code)
    except rbac_service.RoleNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()


@router.get(
    "/me/permissions",
    response_model=MyPermissionsResponse,
    summary="Get the current caller's effective permissions",
)
def read_my_permissions(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> MyPermissionsResponse:
    codes = rbac_service.get_user_permission_codes(db, current_user.id)
    return MyPermissionsResponse(permission_codes=sorted(codes))


__all__ = ["router"]
