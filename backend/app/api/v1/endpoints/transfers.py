"""Stock Transfer endpoints: ``/api/v1/transfers``.

Thin HTTP wrappers around ``services.stock_transfer_service`` -- business
rules live there, per this project's layering rule.  Every mutating
endpoint is gated behind ``TRANSFER_MANAGE`` via ``require_permission``;
reads require only an authenticated caller, matching the convention every
other domain endpoint in this codebase documents.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.transfers import (
    TransferCreateRequest,
    TransferHistoryListResponse,
    TransferHistoryResponse,
    TransferLineListResponse,
    TransferLineResponse,
    TransferListResponse,
    TransferResponse,
    TransferTransitionRequest,
)
from database.models.app_user import AppUser
from services import stock_transfer_service

router = APIRouter(prefix="/transfers", tags=["transfers"])

_require_transfer_manage = require_permission(
    stock_transfer_service.TRANSFER_MANAGE_PERMISSION_CODE
)

#: Map service-layer exceptions to the HTTP status they should surface.
_ERROR_STATUS_MAP: tuple[tuple[type[Exception], int], ...] = (
    (stock_transfer_service.TransferNotFoundError, status.HTTP_404_NOT_FOUND),
    (stock_transfer_service.InvalidTransferStateTransitionError, status.HTTP_409_CONFLICT),
    (stock_transfer_service.WarehouseNotFoundError, status.HTTP_404_NOT_FOUND),
    (stock_transfer_service.SameWarehouseError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (stock_transfer_service.EmptyTransferError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (stock_transfer_service.TransferNotCancellableError, status.HTTP_409_CONFLICT),
)


def _run(func, /, *args, **kwargs):
    """Call a stock_transfer_service function, translating its documented
    exceptions into the matching HTTPException via ``_ERROR_STATUS_MAP``."""
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        for exc_type, http_status in _ERROR_STATUS_MAP:
            if isinstance(exc, exc_type):
                raise HTTPException(http_status, detail=str(exc)) from exc
        raise


def _to_response(transfer, lines=None) -> TransferResponse:
    response = TransferResponse.model_validate(transfer)
    if lines is not None:
        response.lines = [TransferLineResponse.model_validate(line) for line in lines]
    return response


@router.post(
    "",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft stock transfer",
)
def create_transfer(
    body: TransferCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_transfer_manage),
) -> TransferResponse:
    transfer = _run(
        stock_transfer_service.create_transfer,
        db,
        source_warehouse_id=body.source_warehouse_id,
        destination_warehouse_id=body.destination_warehouse_id,
        lines=[
            stock_transfer_service.TransferLineInput(
                product_id=line.product_id,
                qty_requested=line.qty_requested,
                unit_cost=line.unit_cost,
                lot_id=line.lot_id,
            )
            for line in body.lines
        ],
        requested_by=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(transfer)
    lines = stock_transfer_service.list_transfer_lines(db, transfer.id)
    return _to_response(transfer, lines)


@router.get("", response_model=TransferListResponse, summary="List stock transfers")
def list_transfers(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    source_warehouse_id: uuid.UUID | None = Query(default=None),
    destination_warehouse_id: uuid.UUID | None = Query(default=None),
    state: str | None = Query(default=None),
) -> TransferListResponse:
    transfers = stock_transfer_service.list_transfers(
        db,
        source_warehouse_id=source_warehouse_id,
        destination_warehouse_id=destination_warehouse_id,
        state=state,
        skip=skip,
        limit=limit,
    )
    return TransferListResponse(items=[_to_response(t) for t in transfers])


@router.get("/{transfer_id}", response_model=TransferResponse, summary="Get a stock transfer and its lines")
def read_transfer(
    transfer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> TransferResponse:
    transfer = _run(stock_transfer_service.get_transfer, db, transfer_id)
    lines = stock_transfer_service.list_transfer_lines(db, transfer.id)
    return _to_response(transfer, lines)


@router.get(
    "/{transfer_id}/lines",
    response_model=TransferLineListResponse,
    summary="List a transfer's lines",
)
def read_transfer_lines(
    transfer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> TransferLineListResponse:
    lines = _run(stock_transfer_service.list_transfer_lines, db, transfer_id)
    return TransferLineListResponse(items=[TransferLineResponse.model_validate(line) for line in lines])


@router.get(
    "/{transfer_id}/history",
    response_model=TransferHistoryListResponse,
    summary="Get a transfer's state-transition history",
)
def read_transfer_history(
    transfer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> TransferHistoryListResponse:
    history = _run(stock_transfer_service.get_transfer_history, db, transfer_id)
    return TransferHistoryListResponse(
        items=[TransferHistoryResponse.model_validate(h) for h in history]
    )


@router.post(
    "/{transfer_id}/dispatch",
    response_model=TransferResponse,
    summary="DRAFT -> DISPATCHED (posts TRANSFER_OUT from source warehouse)",
)
def dispatch_transfer(
    transfer_id: uuid.UUID,
    body: TransferTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_transfer_manage),
) -> TransferResponse:
    transfer = _run(
        stock_transfer_service.dispatch_transfer,
        db,
        transfer_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(transfer)
    return _to_response(transfer)


@router.post(
    "/{transfer_id}/receive",
    response_model=TransferResponse,
    summary="DISPATCHED -> RECEIVED (posts TRANSFER_IN to destination warehouse)",
)
def receive_transfer(
    transfer_id: uuid.UUID,
    body: TransferTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_transfer_manage),
) -> TransferResponse:
    transfer = _run(
        stock_transfer_service.receive_transfer,
        db,
        transfer_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(transfer)
    return _to_response(transfer)


@router.post(
    "/{transfer_id}/cancel",
    response_model=TransferResponse,
    summary="DRAFT -> CANCELLED",
)
def cancel_transfer(
    transfer_id: uuid.UUID,
    body: TransferTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_transfer_manage),
) -> TransferResponse:
    transfer = _run(
        stock_transfer_service.cancel_transfer,
        db,
        transfer_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(transfer)
    return _to_response(transfer)


__all__ = ["router"]
