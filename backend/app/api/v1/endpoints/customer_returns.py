"""Customer Return endpoints: ``/api/v1/customer-returns``.

Thin HTTP wrappers around ``services.return_service`` -- business rules
live there, per this project's layering rule.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.customer_return import (
    CustomerReturnCreateRequest,
    CustomerReturnListResponse,
    CustomerReturnResponse,
    ReturnLineResponse,
    ReturnTransitionRequest,
)
from database.models.app_user import AppUser
from services import return_service

router = APIRouter(prefix="/customer-returns", tags=["customer-returns"])

RETURN_MANAGE_PERMISSION_CODE = "RETURN_MANAGE"
_require_return_manage = require_permission(RETURN_MANAGE_PERMISSION_CODE)

_ERROR_STATUS_MAP: tuple[tuple[type[Exception], int], ...] = (
    (return_service.ReturnNotFoundError, status.HTTP_404_NOT_FOUND),
    (return_service.InvalidReturnStateTransitionError, status.HTTP_409_CONFLICT),
    (return_service.ReturnAlreadyClosedError, status.HTTP_409_CONFLICT),
    (ValueError, status.HTTP_422_UNPROCESSABLE_ENTITY),
)


def _run(func, /, *args, **kwargs):
    """Call a return_service function, translating exceptions to HTTP status."""
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        for exc_type, http_status in _ERROR_STATUS_MAP:
            if isinstance(exc, exc_type):
                raise HTTPException(http_status, detail=str(exc)) from exc
        raise


def _to_response(cr, lines=None) -> CustomerReturnResponse:
    response = CustomerReturnResponse.model_validate(cr)
    if lines is not None:
        response.lines = [ReturnLineResponse.model_validate(l) for l in lines]
    return response


@router.post(
    "",
    response_model=CustomerReturnResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer return",
)
def create_return(
    body: CustomerReturnCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_return_manage),
) -> CustomerReturnResponse:
    """Create a new customer return in PENDING_APPROVAL state."""
    cr = _run(
        return_service.create_return,
        db,
        customer_id=body.customer_id,
        representative_id=body.representative_id,
        warehouse_id=body.warehouse_id,
        reason_code_id=body.reason_code_id,
        return_type=body.return_type.value,
        order_id=body.order_id,
        actor_user_id=current_user.id,
        note=body.note,
        lines=[line.model_dump() for line in body.lines],
    )
    db.commit()
    db.refresh(cr)
    lines = return_service.get_return_lines(db, cr.id)
    return _to_response(cr, lines)


@router.get(
    "",
    response_model=CustomerReturnListResponse,
    summary="List customer returns",
)
def list_returns(
    customer_id: uuid.UUID | None = Query(default=None),
    state: str | None = Query(default=None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> CustomerReturnListResponse:
    """List customer returns with optional filters."""
    returns = return_service.list_returns(
        db, customer_id=customer_id, state=state, skip=skip, limit=limit,
    )
    return CustomerReturnListResponse(
        items=[_to_response(cr) for cr in returns]
    )


@router.get(
    "/{return_id}",
    response_model=CustomerReturnResponse,
    summary="Get a customer return",
)
def get_return(
    return_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> CustomerReturnResponse:
    """Get a single customer return with its lines."""
    cr = _run(return_service.get_return, db, return_id)
    lines = return_service.get_return_lines(db, return_id)
    return _to_response(cr, lines)


@router.post(
    "/{return_id}/receive",
    response_model=CustomerReturnResponse,
    summary="Receive a customer return",
)
def receive_return(
    return_id: uuid.UUID,
    body: ReturnTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_return_manage),
) -> CustomerReturnResponse:
    """Transition: APPROVED → RECEIVED (physical receipt at warehouse)."""
    cr = _run(
        return_service.receive_return,
        db, return_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(cr)
    lines = return_service.get_return_lines(db, return_id)
    return _to_response(cr, lines)


@router.post(
    "/{return_id}/inspect",
    response_model=CustomerReturnResponse,
    summary="Inspect a customer return",
)
def inspect_return(
    return_id: uuid.UUID,
    body: ReturnTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_return_manage),
) -> CustomerReturnResponse:
    """Transition: RECEIVED → INSPECTED (warehouse inspection complete)."""
    cr = _run(
        return_service.inspect_return,
        db, return_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(cr)
    lines = return_service.get_return_lines(db, return_id)
    return _to_response(cr, lines)


@router.post(
    "/{return_id}/close",
    response_model=CustomerReturnResponse,
    summary="Close a customer return",
)
def close_return(
    return_id: uuid.UUID,
    body: ReturnTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_return_manage),
) -> CustomerReturnResponse:
    """Close a return and trigger commission clawback for DIRECT orders."""
    cr = _run(
        return_service.close_return,
        db, return_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(cr)
    lines = return_service.get_return_lines(db, return_id)
    return _to_response(cr, lines)


__all__ = ["router"]
