"""Commission endpoints: ``/api/v1/commission-configs``.

Thin HTTP wrappers around ``services.commission_service`` -- business rules
live there, per this project's layering rule.  Every mutating endpoint is
gated behind ``COMMISSION_MANAGE`` via ``require_permission``; reads require
only an authenticated caller, matching the convention every other domain
endpoint in this codebase documents.

Representative scope:
``GET /commission-transactions`` enforces representative scope: representative-
linked users can only see their own transactions.  Explicitly querying a
different representative's transactions is rejected with 403 (not silently
overridden) to match the POST /orders convention.  Admin/staff users retain
unscoped read access.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import _require_order_scope, require_permission
from app.schemas.commissions import (
    CommissionApproveRequest,
    CommissionCalculateRequest,
    CommissionClawbackRequest,
    CommissionConfigCreateRequest,
    CommissionConfigListResponse,
    CommissionConfigResponse,
    CommissionPayRequest,
    CommissionTransactionListResponse,
    CommissionTransactionResponse,
)
from database.models.app_user import AppUser
from services import commission_service

router = APIRouter(tags=["commissions"])

_require_commission_manage = require_permission(
    commission_service.COMMISSION_MANAGE_PERMISSION_CODE
)

#: Map service-layer exceptions to the HTTP status they should surface.
_ERROR_STATUS_MAP: tuple[tuple[type[Exception], int], ...] = (
    (commission_service.CommissionConfigNotFoundError, status.HTTP_404_NOT_FOUND),
    (commission_service.CommissionAlreadyCalculatedError, status.HTTP_409_CONFLICT),
    (commission_service.CommissionTransactionNotFoundError, status.HTTP_404_NOT_FOUND),
    (commission_service.InvalidCommissionStateError, status.HTTP_409_CONFLICT),
    (commission_service.NoCommissionConfigFoundError, status.HTTP_404_NOT_FOUND),
    (commission_service.OrderNotCompletedError, status.HTTP_409_CONFLICT),
)


def _run(func, /, *args, **kwargs):
    """Call a commission_service function, translating its documented
    exceptions into the matching HTTPException via ``_ERROR_STATUS_MAP``."""
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        for exc_type, http_status in _ERROR_STATUS_MAP:
            if isinstance(exc, exc_type):
                raise HTTPException(http_status, detail=str(exc)) from exc
        raise


@router.post(
    "/commission-configs",
    response_model=CommissionConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a commission rate configuration",
)
def create_commission_config(
    body: CommissionConfigCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_commission_manage),
) -> CommissionConfigResponse:
    config = _run(
        commission_service.create_commission_config,
        db,
        rate=body.rate,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        representative_id=body.representative_id,
        product_category_id=body.product_category_id,
        order_type=body.order_type.value,
        actor_user_id=current_user.id,
    )
    db.commit()
    db.refresh(config)
    return CommissionConfigResponse.model_validate(config)


@router.get(
    "/commission-configs",
    response_model=CommissionConfigListResponse,
    summary="List all commission configurations",
)
def list_commission_configs(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> CommissionConfigListResponse:
    configs = commission_service.list_commission_configs(db)
    return CommissionConfigListResponse(
        items=[CommissionConfigResponse.model_validate(c) for c in configs]
    )


@router.post(
    "/orders/{order_id}/commission",
    response_model=CommissionTransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Calculate and record commission for a COMPLETED order",
)
def calculate_commission(
    order_id: uuid.UUID,
    body: CommissionCalculateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_commission_manage),
) -> CommissionTransactionResponse:
    # Order scope: verify the referenced order belongs to the caller's
    # representative (or that the caller is admin/staff).  Uses the
    # existing _require_order_scope helper which raises 404 for
    # out-of-scope or non-existent orders, preventing existence leakage.
    # Must happen before commission service to prevent side effects.
    _require_order_scope(order_id, current_user, db)
    txn = _run(
        commission_service.calculate_commission_for_order,
        db,
        order_id=order_id,
        actor_user_id=current_user.id,
    )
    db.commit()
    db.refresh(txn)
    return CommissionTransactionResponse.model_validate(txn)


@router.get(
    "/commission-transactions",
    response_model=CommissionTransactionListResponse,
    summary="List commission transactions",
)
def list_commission_transactions(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    representative_id: uuid.UUID | None = Query(default=None),
    state_event: str | None = Query(default=None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> CommissionTransactionListResponse:
    # Server-side representative scope: representative-linked users
    # can only see their own commission transactions.
    #
    # If the caller explicitly supplied a different representative_id,
    # reject with 403 (same pattern as POST /orders which rejects
    # cross-rep order creation with 403).  If the caller omitted
    # representative_id or supplied their own, force it to their own.
    # Admin/staff users (no representative link) retain the existing
    # optional-filter behavior.
    if current_user.representative_id is not None:
        if (
            representative_id is not None
            and representative_id != current_user.representative_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot list commission transactions for a different representative.",
            )
        representative_id = current_user.representative_id

    txns = commission_service.list_commission_transactions(
        db,
        representative_id=representative_id,
        state_event=state_event,
        skip=skip,
        limit=limit,
    )
    return CommissionTransactionListResponse(
        items=[CommissionTransactionResponse.model_validate(t) for t in txns]
    )


@router.get(
    "/commission-transactions/{transaction_id}",
    response_model=CommissionTransactionResponse,
    summary="Get a commission transaction",
)
def get_commission_transaction(
    transaction_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> CommissionTransactionResponse:
    txn = _run(commission_service.get_commission_transaction, db, transaction_id)
    return CommissionTransactionResponse.model_validate(txn)


@router.post(
    "/commission-transactions/{transaction_id}/approve",
    response_model=CommissionTransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Approve an ACCRUED commission transaction",
)
def approve_commission(
    transaction_id: uuid.UUID,
    body: CommissionApproveRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_commission_manage),
) -> CommissionTransactionResponse:
    new_txn = _run(
        commission_service.approve_commission,
        db,
        transaction_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    # Explicitly fetch the new row (append-only: approve creates a new row).
    result = db.get(commission_service.CommissionTransaction, new_txn.id)
    return CommissionTransactionResponse.model_validate(result)


@router.post(
    "/commission-transactions/{transaction_id}/pay",
    response_model=CommissionTransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Pay an APPROVED commission transaction",
)
def pay_commission(
    transaction_id: uuid.UUID,
    body: CommissionPayRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_commission_manage),
) -> CommissionTransactionResponse:
    new_txn = _run(
        commission_service.pay_commission,
        db,
        transaction_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    result = db.get(commission_service.CommissionTransaction, new_txn.id)
    return CommissionTransactionResponse.model_validate(result)


@router.post(
    "/commission-transactions/{transaction_id}/clawback",
    response_model=CommissionTransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Clawback an ACCRUED or APPROVED commission transaction",
)
def clawback_commission(
    transaction_id: uuid.UUID,
    body: CommissionClawbackRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_commission_manage),
) -> CommissionTransactionResponse:
    new_txn = _run(
        commission_service.clawback_commission,
        db,
        transaction_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    result = db.get(commission_service.CommissionTransaction, new_txn.id)
    return CommissionTransactionResponse.model_validate(result)


@router.get(
    "/representatives/{representative_id}/commission-balance",
    response_model=dict,
    summary="Get net commission balance for a representative",
)
def get_commission_balance(
    representative_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    # Representative scope: a representative-linked user may only
    # query their own commission balance.  Admin/staff users (no
    # representative link) may query any representative.
    #
    # 403 for cross-representative access (the rep exists but the
    # caller has no right to see it); 404 for a genuinely
    # nonexistent representative_id -- matching the distinction the
    # task requests.
    if current_user.representative_id is not None:
        if current_user.representative_id != representative_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only query your own commission balance.",
            )

    # Verify the representative actually exists (404 if not).
    from database.models.representative import Representative
    from sqlalchemy import select as _sel

    rep_exists = db.execute(
        _sel(Representative.id).where(Representative.id == representative_id)
    ).scalar_one_or_none()
    if rep_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Representative not found.",
        )

    balance = commission_service.get_representative_commission_balance(
        db, representative_id,
    )
    return {"representative_id": str(representative_id), "balance": str(balance)}


__all__ = ["router"]
