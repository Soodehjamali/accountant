"""Payment endpoints: ``/api/v1/payments``.

Thin HTTP wrappers around ``services.payment_service`` -- business rules
live there, per this project's layering rule.  Every mutating endpoint is
gated behind ``PAYMENT_MANAGE`` via ``require_permission``; reads require
only an authenticated caller, matching the convention every other domain
endpoint in this codebase documents.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.payments import (
    PaymentAllocationListResponse,
    PaymentAllocationResponse,
    PaymentCreateRequest,
    PaymentListResponse,
    PaymentResponse,
)
from database.models.app_user import AppUser
from services import customer_ledger_service, payment_service

router = APIRouter(tags=["payments"])

_require_payment_manage = require_permission(
    payment_service.PAYMENT_MANAGE_PERMISSION_CODE
)

#: Map service-layer exceptions to the HTTP status they should surface.
_ERROR_STATUS_MAP: tuple[tuple[type[Exception], int], ...] = (
    (payment_service.PaymentNotFoundError, status.HTTP_404_NOT_FOUND),
    (payment_service.PaymentExceedsTotalAllocationsError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (payment_service.InvoiceAllocationExceedsBalanceError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (payment_service.InvoiceNotPayableError, status.HTTP_409_CONFLICT),
)


def _run(func, /, *args, **kwargs):
    """Call a payment_service function, translating its documented
    exceptions into the matching HTTPException via ``_ERROR_STATUS_MAP``."""
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        for exc_type, http_status in _ERROR_STATUS_MAP:
            if isinstance(exc, exc_type):
                raise HTTPException(http_status, detail=str(exc)) from exc
        raise


def _to_response(payment, allocations=None) -> PaymentResponse:
    response = PaymentResponse.model_validate(payment)
    if allocations is not None:
        response.allocations = [
            PaymentAllocationResponse.model_validate(a) for a in allocations
        ]
    return response


@router.post(
    "/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payment with allocations to one or more invoices",
)
def create_payment(
    body: PaymentCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_payment_manage),
) -> PaymentResponse:
    payment = _run(
        payment_service.record_payment,
        db,
        customer_id=body.customer_id,
        currency_id=body.currency_id,
        amount=body.amount,
        method=body.method.value,
        allocations=[(a.invoice_id, a.allocated_amount) for a in body.allocations],
        actor_user_id=current_user.id,
        reference=body.reference,
        received_at=body.received_at,
        record_entry=customer_ledger_service.record_entry,
    )
    db.commit()
    db.refresh(payment)
    allocations = payment_service.list_allocations_for_payment(db, payment.id)
    return _to_response(payment, allocations)


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentResponse,
    summary="Get a payment and its allocations",
)
def read_payment(
    payment_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> PaymentResponse:
    payment = _run(payment_service.get_payment, db, payment_id)
    allocations = payment_service.list_allocations_for_payment(db, payment.id)
    return _to_response(payment, allocations)


@router.get(
    "/invoices/{invoice_id}/payments",
    response_model=PaymentListResponse,
    summary="List all payments allocated to an invoice",
)
def read_invoice_payments(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> PaymentListResponse:
    payments = payment_service.list_payments_for_invoice(db, invoice_id)
    return PaymentListResponse(items=[_to_response(p) for p in payments])


__all__ = ["router"]
