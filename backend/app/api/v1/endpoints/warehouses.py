"""Warehouse endpoints: ``/api/v1/warehouses``.

Thin HTTP wrappers around ``services.warehouse_service``.
Mutations gated behind ``WAREHOUSE_MANAGE`` permission.
Reads require only an authenticated caller.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.warehouse import (
    WarehouseAssignmentCreateRequest,
    WarehouseAssignmentListResponse,
    WarehouseAssignmentResponse,
    WarehouseCreateRequest,
    WarehouseListResponse,
    WarehouseResponse,
    WarehouseUpdateRequest,
)
from database.models.app_user import AppUser
from services import warehouse_service

router = APIRouter(prefix="/warehouses", tags=["warehouses"])

WAREHOUSE_MANAGE_PERMISSION_CODE = "WAREHOUSE_MANAGE"
_require_warehouse_manage = require_permission(WAREHOUSE_MANAGE_PERMISSION_CODE)


# ---------------------------------------------------------------------------
# Warehouse CRUD
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=WarehouseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a warehouse",
)
def create_warehouse(
    body: WarehouseCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_warehouse_manage),
) -> WarehouseResponse:
    try:
        wh = warehouse_service.create_warehouse(
            db,
            code=body.code,
            name=body.name,
            type=body.type.value,
            ownership_mode=body.ownership_mode.value,
            address=body.address,
            city_ref_id=body.city_ref_id,
            latitude=body.latitude,
            longitude=body.longitude,
            responsible_user_id=body.responsible_user_id,
            created_by=current_user.id,
        )
    except warehouse_service.DuplicateWarehouseCodeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(wh)
    return wh


@router.get("", response_model=WarehouseListResponse, summary="List warehouses")
def list_warehouses(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(default=None),
    type_: str | None = Query(default=None, alias="type"),
    status_: str | None = Query(default=None, alias="status"),
) -> WarehouseListResponse:
    items = warehouse_service.list_warehouses(
        db, type=type_, status=status_, search=search, skip=skip, limit=limit
    )
    return WarehouseListResponse(items=list(items))


@router.get("/{warehouse_id}", response_model=WarehouseResponse, summary="Get a warehouse")
def read_warehouse(
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> WarehouseResponse:
    try:
        return warehouse_service.get_warehouse(db, warehouse_id)
    except warehouse_service.WarehouseNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{warehouse_id}", response_model=WarehouseResponse, summary="Update a warehouse")
def update_warehouse(
    warehouse_id: uuid.UUID,
    body: WarehouseUpdateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_warehouse_manage),
) -> WarehouseResponse:
    try:
        wh = warehouse_service.update_warehouse(
            db,
            warehouse_id,
            updated_by=current_user.id,
            name=body.name,
            address=body.address,
            city_ref_id=body.city_ref_id,
            latitude=body.latitude,
            longitude=body.longitude,
            responsible_user_id=body.responsible_user_id,
            status=body.status.value if body.status is not None else None,
        )
    except warehouse_service.WarehouseNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    db.refresh(wh)
    return wh


@router.post(
    "/{warehouse_id}/deactivate",
    response_model=WarehouseResponse,
    summary="Deactivate a warehouse (status -> INACTIVE)",
)
def deactivate_warehouse(
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_warehouse_manage),
) -> WarehouseResponse:
    try:
        wh = warehouse_service.deactivate_warehouse(
            db, warehouse_id, updated_by=current_user.id,
        )
    except warehouse_service.WarehouseNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except warehouse_service.WarehouseNotDeactivatableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(wh)
    return wh


# ---------------------------------------------------------------------------
# Warehouse Assignments
# ---------------------------------------------------------------------------

@router.post(
    "/{warehouse_id}/assignments",
    response_model=WarehouseAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a representative to a warehouse",
)
def create_assignment(
    warehouse_id: uuid.UUID,
    body: WarehouseAssignmentCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_warehouse_manage),
) -> WarehouseAssignmentResponse:
    try:
        assignment = warehouse_service.create_assignment(
            db,
            representative_id=body.representative_id,
            warehouse_id=warehouse_id,
            is_primary=body.is_primary,
            effective_from=body.effective_from,
            effective_to=body.effective_to,
            created_by=current_user.id,
        )
    except warehouse_service.WarehouseNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except warehouse_service.DuplicateAssignmentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(assignment)
    return assignment


@router.get(
    "/{warehouse_id}/assignments",
    response_model=WarehouseAssignmentListResponse,
    summary="List representatives assigned to a warehouse",
)
def list_assignments_for_warehouse(
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> WarehouseAssignmentListResponse:
    items = warehouse_service.list_assignments_for_warehouse(db, warehouse_id)
    return WarehouseAssignmentListResponse(items=list(items))


@router.delete(
    "/{warehouse_id}/assignments/{representative_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a warehouse assignment",
)
def delete_assignment(
    warehouse_id: uuid.UUID,
    representative_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_warehouse_manage),
) -> None:
    try:
        warehouse_service.delete_assignment(
            db,
            representative_id=representative_id,
            warehouse_id=warehouse_id,
        )
    except warehouse_service.AssignmentNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()


__all__ = ["router"]
