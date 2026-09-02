"""Unit of Measure endpoints: ``/api/v1/units-of-measure``.

Read-only endpoint for the ``unit_of_measure`` (R2) reference catalog.
Any authenticated user can list units of measure — this is reference data,
not a mutation-gated resource.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from database.models.app_user import AppUser
from database.models.unit_of_measure import UnitOfMeasure

router = APIRouter(tags=["units-of-measure"])


class UnitOfMeasureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    class_: str


class UnitOfMeasureListResponse(BaseModel):
    items: list[UnitOfMeasureResponse]


@router.get(
    "/units-of-measure",
    response_model=UnitOfMeasureListResponse,
    summary="List units of measure",
)
def list_units_of_measure(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
    class_: str | None = Query(
        default=None,
        description="Filter by class (BASE, DERIVED)",
    ),
) -> UnitOfMeasureListResponse:
    """Return all units of measure, optionally filtered by class.

    Any authenticated user can read this — it's reference data shared
    across multiple domains (products, transfers, inventory, etc.).
    """
    query = select(UnitOfMeasure)
    if class_ is not None:
        query = query.where(UnitOfMeasure.class_ == class_)
    query = query.order_by(UnitOfMeasure.code)
    rows = db.execute(query).scalars().all()
    return UnitOfMeasureListResponse(
        items=[UnitOfMeasureResponse.model_validate(r) for r in rows]
    )


PRODUCT_MANAGE_PERMISSION_CODE = "PRODUCT_MANAGE"
_require_product_manage = require_permission(PRODUCT_MANAGE_PERMISSION_CODE)


class UnitOfMeasureUpdateRequest(BaseModel):
    """Request body for ``PATCH /units-of-measure/{id}``."""

    name: str | None = None
    class_: str | None = None


@router.patch(
    "/units-of-measure/{uom_id}",
    response_model=UnitOfMeasureResponse,
    summary="Update a unit of measure",
)
def update_unit_of_measure(
    uom_id: uuid.UUID,
    body: UnitOfMeasureUpdateRequest,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_product_manage),
) -> UnitOfMeasureResponse:
    """Patch-update a unit of measure. Only non-None fields are applied."""
    uom = db.execute(
        select(UnitOfMeasure).where(UnitOfMeasure.id == uom_id)
    ).scalar_one_or_none()
    if uom is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"No unit of measure with id '{uom_id}' exists.",
        )

    if body.name is not None:
        uom.name = body.name
    if body.class_ is not None:
        uom.class_ = body.class_
    db.commit()
    db.refresh(uom)
    return uom


class UnitOfMeasureInUseError(ValueError):
    """Raised when attempting to delete a UoM that is still referenced."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@router.delete(
    "/units-of-measure/{uom_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a unit of measure",
)
def delete_unit_of_measure(
    uom_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_product_manage),
) -> None:
    """Hard-delete a unit of measure if it is not referenced.

    Checks for: products using this as base UoM, UoM conversions.
    Returns HTTP 409 if still in use.
    """
    from database.models.product import Product
    from database.models.uom_conversion import UomConversion

    uom = db.execute(
        select(UnitOfMeasure).where(UnitOfMeasure.id == uom_id)
    ).scalar_one_or_none()
    if uom is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"No unit of measure with id '{uom_id}' exists.",
        )

    refs = []

    # Check products using this as base UoM.
    product_count = db.execute(
        select(sqlfunc.count()).select_from(Product).where(
            Product.base_uom_id == uom_id
        )
    ).scalar_one()
    if product_count > 0:
        refs.append(f"{product_count} products")

    # Check UoM conversions (from_uom_id or to_uom_id).
    from_count = db.execute(
        select(sqlfunc.count()).select_from(UomConversion).where(
            UomConversion.from_uom_id == uom_id
        )
    ).scalar_one()
    if from_count > 0:
        refs.append(f"{from_count} UoM conversions (source)")

    to_count = db.execute(
        select(sqlfunc.count()).select_from(UomConversion).where(
            UomConversion.to_uom_id == uom_id
        )
    ).scalar_one()
    if to_count > 0:
        refs.append(f"{to_count} UoM conversions (target)")

    if refs:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Cannot delete: still referenced by {', '.join(refs)}",
        )

    db.delete(uom)
    db.commit()


__all__ = ["router"]
