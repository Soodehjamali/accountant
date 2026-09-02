"""Unit of Measure endpoints: ``/api/v1/units-of-measure``.

Read-only endpoint for the ``unit_of_measure`` (R2) reference catalog.
Any authenticated user can list units of measure — this is reference data,
not a mutation-gated resource.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
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


__all__ = ["router"]
