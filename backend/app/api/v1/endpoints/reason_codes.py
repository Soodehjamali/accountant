"""Reason Code endpoints: ``/api/v1/reason-codes``.

Read-only endpoint for the ``reason_code_ref`` (R11) reference catalog.
Any authenticated user can list reason codes — this is reference data,
not a mutation-gated resource.

The optional ``scope`` query parameter filters by the CHECK-constrained
vocabulary: ADJUSTMENT, VARIANCE, RETURN, DAMAGE.  Used by both the
Credit Note milestone (reason_code_id on creation) and the upcoming
Inventory milestone (stock_adjustment/physical_count also reference it).
"""

from __future__ import annotations

from typing import Literal

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from database.models.app_user import AppUser
from database.models.reason_code_ref import ReasonCodeRef

router = APIRouter(tags=["reason-codes"])


class ReasonCodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    label: str
    scope: str


class ReasonCodeListResponse(BaseModel):
    items: list[ReasonCodeResponse]


@router.get(
    "/reason-codes",
    response_model=ReasonCodeListResponse,
    summary="List active reason codes",
)
def list_reason_codes(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
    scope: Literal["ADJUSTMENT", "VARIANCE", "RETURN", "DAMAGE"] | None = Query(
        default=None,
        description="Filter by scope (ADJUSTMENT, VARIANCE, RETURN, DAMAGE)",
    ),
) -> ReasonCodeListResponse:
    """Return all active reason codes, optionally filtered by scope.

    Any authenticated user can read this — it's reference data shared
    across multiple domains (credit notes, inventory adjustments, etc.).
    """
    query = select(ReasonCodeRef)
    if scope is not None:
        query = query.where(ReasonCodeRef.scope == scope)
    query = query.order_by(ReasonCodeRef.code)
    rows = db.execute(query).scalars().all()
    return ReasonCodeListResponse(
        items=[ReasonCodeResponse.model_validate(r) for r in rows]
    )


__all__ = ["router"]
