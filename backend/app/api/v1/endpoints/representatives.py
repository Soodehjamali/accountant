"""Representative endpoints: ``/api/v1/representatives``.

Thin HTTP wrappers around ``services.representative_service``.
Mutations gated behind ``REPRESENTATIVE_MANAGE`` permission.
Reads require only an authenticated caller.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.representative import (
    RepresentativeCreateRequest,
    RepresentativeListResponse,
    RepresentativeResponse,
    RepresentativeUpdateRequest,
)
from database.models.app_user import AppUser
from services import representative_service

router = APIRouter(prefix="/representatives", tags=["representatives"])

REPRESENTATIVE_MANAGE_PERMISSION_CODE = "REPRESENTATIVE_MANAGE"
_require_representative_manage = require_permission(REPRESENTATIVE_MANAGE_PERMISSION_CODE)


@router.post(
    "",
    response_model=RepresentativeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a representative",
)
def create_representative(
    body: RepresentativeCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_representative_manage),
) -> RepresentativeResponse:
    try:
        rep = representative_service.create_representative(
            db,
            code=body.code,
            person_name=body.person_name,
            national_id=body.national_id,
            tax_id=body.tax_id,
            home_city_ref_id=body.home_city_ref_id,
            created_by=current_user.id,
        )
    except representative_service.DuplicateRepresentativeCodeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # If a phone number was provided, create a PHONE contact record.
    if body.phone_number:
        from database.models.representative_contact import RepresentativeContact

        phone_contact = RepresentativeContact(
            representative_id=rep.id,
            kind="PHONE",
            value=body.phone_number,
            is_primary=True,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(phone_contact)

    db.commit()
    db.refresh(rep)
    return rep


@router.get("", response_model=RepresentativeListResponse, summary="List representatives")
def list_representatives(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
) -> RepresentativeListResponse:
    items = representative_service.list_representatives(
        db, status=status_, search=search, skip=skip, limit=limit
    )
    return RepresentativeListResponse(items=list(items))


@router.get("/{representative_id}", response_model=RepresentativeResponse, summary="Get a representative")
def read_representative(
    representative_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> RepresentativeResponse:
    try:
        return representative_service.get_representative(db, representative_id)
    except representative_service.RepresentativeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{representative_id}", response_model=RepresentativeResponse, summary="Update a representative")
def update_representative(
    representative_id: uuid.UUID,
    body: RepresentativeUpdateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_representative_manage),
) -> RepresentativeResponse:
    try:
        rep = representative_service.update_representative(
            db,
            representative_id,
            updated_by=current_user.id,
            person_name=body.person_name,
            national_id=body.national_id,
            tax_id=body.tax_id,
            home_city_ref_id=body.home_city_ref_id,
            status=body.status.value if body.status is not None else None,
        )
    except representative_service.RepresentativeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    db.refresh(rep)
    return rep


@router.post(
    "/{representative_id}/deactivate",
    response_model=RepresentativeResponse,
    summary="Deactivate a representative (status -> OFFBOARDED)",
)
def deactivate_representative(
    representative_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_representative_manage),
) -> RepresentativeResponse:
    try:
        rep = representative_service.deactivate_representative(
            db, representative_id, updated_by=current_user.id,
        )
    except representative_service.RepresentativeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except representative_service.RepresentativeNotDeactivatableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(rep)
    return rep


@router.delete(
    "/{representative_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a representative",
)
def delete_representative(
    representative_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_representative_manage),
) -> None:
    """Hard-delete a representative if it is not referenced by any other records.

    Returns HTTP 409 if the representative is still in use.
    """
    try:
        representative_service.delete_representative(db, representative_id)
    except representative_service.RepresentativeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except representative_service.RepresentativeInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()


__all__ = ["router"]
