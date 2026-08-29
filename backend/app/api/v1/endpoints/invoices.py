"""Invoice endpoints: ``/api/v1/invoices``.

Thin HTTP wrappers around ``services.invoice_service`` -- business rules
live there, per this project's layering rule.  Every mutating endpoint is
gated behind ``INVOICE_MANAGE`` via ``require_permission``; reads require
only an authenticated caller, matching the convention every other domain
endpoint in this codebase documents.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import _require_invoice_scope, _require_order_scope, require_permission
from app.schemas.invoices import (
    InvoiceCreateFromOrderRequest,
    InvoiceHistoryListResponse,
    InvoiceHistoryResponse,
    InvoiceLineListResponse,
    InvoiceLineResponse,
    InvoiceListResponse,
    InvoicePaymentRequest,
    InvoiceResponse,
    InvoiceTransitionRequest,
)
from database.models.app_user import AppUser
from services import customer_ledger_service, invoice_service

router = APIRouter(prefix="/invoices", tags=["invoices"])

_require_invoice_manage = require_permission(invoice_service.INVOICE_MANAGE_PERMISSION_CODE)

#: Map service-layer exceptions to the HTTP status they should surface.
_ERROR_STATUS_MAP: tuple[tuple[type[Exception], int], ...] = (
    (invoice_service.InvoiceNotFoundError, status.HTTP_404_NOT_FOUND),
    (invoice_service.InvalidInvoiceStateTransitionError, status.HTTP_409_CONFLICT),
    (invoice_service.InvoiceImmutableError, status.HTTP_409_CONFLICT),
    (invoice_service.OrderNotFoundError, status.HTTP_404_NOT_FOUND),
    (invoice_service.OrderNotShippedError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (invoice_service.OrderNotInShippableStateForInvoiceError, status.HTTP_409_CONFLICT),
    (invoice_service.PaymentExceedsBalanceError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (invoice_service.VoidOnlyFromDraftError, status.HTTP_409_CONFLICT),
)

from sqlalchemy.orm.exc import StaleDataError  # noqa: E402


def _run(func, /, *args, **kwargs):
    """Call an invoice_service function, translating its documented
    exceptions into the matching HTTPException via ``_ERROR_STATUS_MAP``."""
    try:
        return func(*args, **kwargs)
    except StaleDataError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Invoice was modified concurrently. Please retry.",
        )
    except Exception as exc:
        for exc_type, http_status in _ERROR_STATUS_MAP:
            if isinstance(exc, exc_type):
                raise HTTPException(http_status, detail=str(exc)) from exc
        raise


def _to_response(invoice, lines=None) -> InvoiceResponse:
    response = InvoiceResponse.model_validate(invoice)
    if lines is not None:
        response.lines = [InvoiceLineResponse.model_validate(line) for line in lines]
    return response


@router.post(
    "/from-order",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft invoice from a shipped order",
)
def create_from_order(
    body: InvoiceCreateFromOrderRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_invoice_manage),
) -> InvoiceResponse:
    # Order scope: verify the referenced order belongs to the caller's
    # representative (or that the caller is admin/staff).  Uses the
    # existing _require_order_scope helper which raises 404 for
    # out-of-scope or non-existent orders, preventing existence leakage.
    _require_order_scope(body.order_id, current_user, db)
    invoice = _run(
        invoice_service.create_invoice_from_order,
        db,
        order_id=body.order_id,
        created_by=current_user.id,
        due_days=body.due_days,
        note=body.note,
    )
    db.commit()
    db.refresh(invoice)
    lines = invoice_service.list_invoice_lines(db, invoice.id)
    return _to_response(invoice, lines)


@router.get("", response_model=InvoiceListResponse, summary="List invoices")
def list_invoices(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    customer_id: uuid.UUID | None = Query(default=None),
    state: str | None = Query(default=None),
) -> InvoiceListResponse:
    # Server-side representative scope: representative-linked users
    # can only see invoices linked to their own orders.  Admin/staff
    # users (no representative link) see all invoices.
    representative_id = (
        current_user.representative_id
        if current_user.representative_id is not None
        else None
    )
    invoices = invoice_service.list_invoices(
        db, customer_id=customer_id, state=state,
        representative_id=representative_id, skip=skip, limit=limit,
    )
    return InvoiceListResponse(items=[_to_response(inv) for inv in invoices])


@router.get("/{invoice_id}", response_model=InvoiceResponse, summary="Get an invoice and its lines")
def read_invoice(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> InvoiceResponse:
    # Invoice scope: verify the invoice is linked to an order belonging
    # to the caller's representative (or that the caller is admin/staff).
    _require_invoice_scope(invoice_id, current_user, db)
    invoice = _run(invoice_service.get_invoice, db, invoice_id)
    lines = invoice_service.list_invoice_lines(db, invoice_id)
    return _to_response(invoice, lines)


@router.get(
    "/{invoice_id}/lines",
    response_model=InvoiceLineListResponse,
    summary="List an invoice's lines",
)
def read_invoice_lines(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> InvoiceLineListResponse:
    _require_invoice_scope(invoice_id, current_user, db)
    lines = _run(invoice_service.list_invoice_lines, db, invoice_id)
    return InvoiceLineListResponse(items=[InvoiceLineResponse.model_validate(line) for line in lines])


@router.get(
    "/{invoice_id}/history",
    response_model=InvoiceHistoryListResponse,
    summary="Get an invoice's state-transition history",
)
def read_invoice_history(
    invoice_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> InvoiceHistoryListResponse:
    _require_invoice_scope(invoice_id, current_user, db)
    history = _run(invoice_service.get_invoice_history, db, invoice_id)
    return InvoiceHistoryListResponse(
        items=[InvoiceHistoryResponse.model_validate(h) for h in history]
    )


@router.post(
    "/{invoice_id}/issue",
    response_model=InvoiceResponse,
    summary="DRAFT -> ISSUED",
)
def issue_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_invoice_manage),
) -> InvoiceResponse:
    _require_invoice_scope(invoice_id, current_user, db)
    invoice = _run(
        invoice_service.issue_invoice,
        db,
        invoice_id,
        actor_user_id=current_user.id,
        note=body.note,
        record_entry=customer_ledger_service.record_entry,
    )
    db.commit()
    db.refresh(invoice)
    return _to_response(invoice)


@router.post(
    "/{invoice_id}/pay",
    response_model=InvoiceResponse,
    summary="Record a payment (ISSUED/PARTIALLY_PAID -> PARTIALLY_PAID/PAID)",
)
def record_payment(
    invoice_id: uuid.UUID,
    body: InvoicePaymentRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_invoice_manage),
) -> InvoiceResponse:
    _require_invoice_scope(invoice_id, current_user, db)
    invoice = _run(
        invoice_service.record_payment,
        db,
        invoice_id,
        amount=body.amount,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(invoice)
    return _to_response(invoice)


@router.post(
    "/{invoice_id}/void",
    response_model=InvoiceResponse,
    summary="DRAFT -> VOID",
)
def void_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_invoice_manage),
) -> InvoiceResponse:
    _require_invoice_scope(invoice_id, current_user, db)
    invoice = _run(
        invoice_service.void_invoice,
        db,
        invoice_id,
        actor_user_id=current_user.id,
        note=body.note,
    )
    db.commit()
    db.refresh(invoice)
    return _to_response(invoice)


__all__ = ["router"]
