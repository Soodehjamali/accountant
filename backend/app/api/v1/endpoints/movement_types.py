"""Movement Type endpoints: ``/api/v1/movement-types``.

Read-only endpoint for the ``movement_type_ref`` (R4) reference catalog.
Any authenticated user can list movement types -- this is reference data,
not a mutation-gated resource.  Used by the Inventory Ledger milestone's
Post Transaction form (movement type dropdown).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from database.models.app_user import AppUser
from database.models.movement_type_ref import MovementTypeRef

router = APIRouter(tags=["movement-types"])


class MovementTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    label: str
    sign: int


class MovementTypeListResponse(BaseModel):
    items: list[MovementTypeResponse]


@router.get(
    "/movement-types",
    response_model=MovementTypeListResponse,
    summary="List inventory movement types",
)
def list_movement_types(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> MovementTypeListResponse:
    """Return all seeded movement types with code, label, and sign.

    Any authenticated user can read this -- it's reference data shared
    across the inventory domain.
    """
    query = select(MovementTypeRef).order_by(MovementTypeRef.code)
    rows = db.execute(query).scalars().all()
    return MovementTypeListResponse(
        items=[MovementTypeResponse.model_validate(r) for r in rows]
    )


__all__ = ["router"]
