"""Price List and Price History endpoints: ``/api/v1/price-lists``.

Thin HTTP wrappers around ``services.price_list_service``.
Mutations gated behind ``PRICE_LIST_MANAGE`` permission.
Reads require only an authenticated caller.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.price_list import (
    PriceEntryCreateRequest,
    PriceEntryListResponse,
    PriceEntryResponse,
    PriceEntryUpdateRequest,
    PriceListCreateRequest,
    PriceListListResponse,
    PriceListResponse,
    PriceListUpdateRequest,
)
from database.models.app_user import AppUser
from services import price_list_service

router = APIRouter(prefix="/price-lists", tags=["price-lists"])

PRICE_LIST_MANAGE_PERMISSION_CODE = "PRICE_LIST_MANAGE"
_require_price_list_manage = require_permission(PRICE_LIST_MANAGE_PERMISSION_CODE)


# ---------------------------------------------------------------------------
# Price List CRUD
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=PriceListResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a price list",
)
def create_price_list(
    body: PriceListCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_price_list_manage),
) -> PriceListResponse:
    try:
        pl = price_list_service.create_price_list(
            db,
            name=body.name,
            price_type=body.price_type.value,
            currency_id=body.currency_id,
            owner_scope=body.owner_scope,
            created_by=current_user.id,
        )
    except price_list_service.DuplicatePriceListNameError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(pl)
    return pl


@router.get("", response_model=PriceListListResponse, summary="List price lists")
def list_price_lists(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(default=None),
    price_type_: str | None = Query(default=None, alias="price_type"),
    is_active_: bool | None = Query(default=None, alias="is_active"),
) -> PriceListListResponse:
    items = price_list_service.list_price_lists(
        db,
        price_type=price_type_,
        is_active=is_active_,
        search=search,
        skip=skip,
        limit=limit,
    )
    return PriceListListResponse(items=list(items))


@router.get("/{price_list_id}", response_model=PriceListResponse, summary="Get a price list")
def read_price_list(
    price_list_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> PriceListResponse:
    try:
        return price_list_service.get_price_list(db, price_list_id)
    except price_list_service.PriceListNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{price_list_id}", response_model=PriceListResponse, summary="Update a price list")
def update_price_list(
    price_list_id: uuid.UUID,
    body: PriceListUpdateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_price_list_manage),
) -> PriceListResponse:
    try:
        pl = price_list_service.update_price_list(
            db,
            price_list_id,
            updated_by=current_user.id,
            name=body.name,
            owner_scope=body.owner_scope,
        )
    except price_list_service.PriceListNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except price_list_service.DuplicatePriceListNameError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(pl)
    return pl


@router.post(
    "/{price_list_id}/deactivate",
    response_model=PriceListResponse,
    summary="Deactivate a price list",
)
def deactivate_price_list(
    price_list_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_price_list_manage),
) -> PriceListResponse:
    try:
        pl = price_list_service.deactivate_price_list(
            db, price_list_id, updated_by=current_user.id,
        )
    except price_list_service.PriceListNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    db.refresh(pl)
    return pl


@router.post(
    "/{price_list_id}/activate",
    response_model=PriceListResponse,
    summary="Activate a price list",
)
def activate_price_list(
    price_list_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_price_list_manage),
) -> PriceListResponse:
    try:
        pl = price_list_service.activate_price_list(
            db, price_list_id, updated_by=current_user.id,
        )
    except price_list_service.PriceListNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    db.refresh(pl)
    return pl


# ---------------------------------------------------------------------------
# Price Entries (Price History)
# ---------------------------------------------------------------------------


@router.post(
    "/{price_list_id}/items",
    response_model=PriceEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a price entry to a price list",
)
def add_price_entry(
    price_list_id: uuid.UUID,
    body: PriceEntryCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_price_list_manage),
) -> PriceEntryResponse:
    try:
        entry = price_list_service.add_price_entry(
            db,
            product_id=body.product_id,
            price_list_id=price_list_id,
            unit_price=body.unit_price,
            effective_from=body.effective_from,
            created_by=current_user.id,
            reason=body.reason,
            is_promo=body.is_promo,
            promo_valid_from=body.promo_valid_from,
            promo_valid_to=body.promo_valid_to,
        )
    except price_list_service.PriceListNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except price_list_service.PriceListNotActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except price_list_service.ProductNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except price_list_service.OverlappingPriceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(entry)
    return entry


@router.get(
    "/{price_list_id}/items",
    response_model=PriceEntryListResponse,
    summary="List price entries for a price list",
)
def list_price_entries(
    price_list_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    product_id: uuid.UUID | None = Query(default=None),
) -> PriceEntryListResponse:
    items = price_list_service.list_price_entries(
        db, price_list_id, product_id=product_id, skip=skip, limit=limit,
    )
    return PriceEntryListResponse(items=list(items))


@router.get(
    "/{price_list_id}/items/{entry_id}",
    response_model=PriceEntryResponse,
    summary="Get a price entry",
)
def read_price_entry(
    price_list_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> PriceEntryResponse:
    try:
        return price_list_service.get_price_entry(db, entry_id)
    except price_list_service.PriceEntryNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{price_list_id}/items/{entry_id}/update-price",
    response_model=PriceEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new price version (closes the previous)",
)
def update_price_entry(
    price_list_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: PriceEntryUpdateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_price_list_manage),
) -> PriceEntryResponse:
    """Create a new price version for the same product in this price list.

    PriceHistory is append-only: this creates a new row and closes the
    previous version's ``effective_to``.
    """
    # Read the existing entry to get product_id.
    try:
        existing = price_list_service.get_price_entry(db, entry_id)
    except price_list_service.PriceEntryNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        new_entry = price_list_service.add_price_entry(
            db,
            product_id=existing.product_id,
            price_list_id=price_list_id,
            unit_price=body.unit_price,
            effective_from=body.effective_from,
            created_by=current_user.id,
            reason=body.reason,
            is_promo=body.is_promo,
            promo_valid_from=body.promo_valid_from,
            promo_valid_to=body.promo_valid_to,
        )
    except price_list_service.PriceListNotActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except price_list_service.ProductNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except price_list_service.OverlappingPriceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(new_entry)
    return new_entry


__all__ = ["router"]
