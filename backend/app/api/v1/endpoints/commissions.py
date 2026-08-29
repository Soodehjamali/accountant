"""Commission endpoints: ``/api/v1/commission-configs``.

Thin HTTP wrappers around ``services.commission_service`` -- business rules
live there, per this project's layering rule.  Every mutating endpoint is
gated behind ``COMMISSION_MANAGE`` via ``require_permission``; reads require
only an authenticated caller, matching the convention every other domain
endpoint in this codebase documents.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import _require_order_scope, require_permission
from app.schemas.commissions import (
    CommissionCalculateRequest,
    CommissionConfigCreateRequest,
    CommissionConfigListResponse,
    CommissionConfigResponse,
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


__all__ = ["router"]
