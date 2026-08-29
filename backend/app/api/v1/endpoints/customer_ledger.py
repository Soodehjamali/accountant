"""Customer Ledger endpoints: ``GET /customers/{id}/ledger``,
``GET /customers/{id}/balance``, ``POST /customers/{id}/ledger/reconcile``.

Read-only wrapper around ``services.customer_ledger_service`` -- the
write path (``record_entry``) is called by other domain services
(invoice, payment, credit_note), not directly over HTTP.

Both read endpoints are gated behind ``CUSTOMER_LEDGER_VIEW``
permission via ``require_permission``, matching
``endpoints/audit_log.py``'s pattern: the customer's AR ledger is
sensitive financial data that should not be readable by "any logged-in
user".  Both ``CUSTOMER_LEDGER_VIEW`` and ``CUSTOMER_LEDGER_MANAGE`` are
auto-seeded in ``bootstrap_service._ADMIN_DEFAULT_PERMISSIONS``, so the
ADMIN role holds them by default.

The reconcile endpoint is gated behind ``CUSTOMER_LEDGER_MANAGE``
since it writes to the cached projection columns -- a privileged
operation matching the spec's "reconciliation role only" constraint.
"""

from __future__ import annotations

import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import _require_customer_scope, require_permission
from app.schemas.customer_ledger import (
    CustomerBalanceResponse,
    CustomerLedgerEntryListResponse,
    CustomerLedgerReconcileResponse,
)
from database.models.app_user import AppUser
from services import customer_ledger_service, customer_service

router = APIRouter(prefix="/customers", tags=["customer-ledger"])

CUSTOMER_LEDGER_VIEW_PERMISSION_CODE = "CUSTOMER_LEDGER_VIEW"
CUSTOMER_LEDGER_MANAGE_PERMISSION_CODE = "CUSTOMER_LEDGER_MANAGE"
_require_customer_ledger_view = require_permission(CUSTOMER_LEDGER_VIEW_PERMISSION_CODE)
_require_customer_ledger_manage = require_permission(
    CUSTOMER_LEDGER_MANAGE_PERMISSION_CODE
)


@router.get(
    "/{customer_id}/ledger",
    response_model=CustomerLedgerEntryListResponse,
    summary="List customer ledger entries (filterable by date range)",
)
def list_customer_ledger(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_customer_ledger_view),
    occurred_from: datetime.datetime | None = Query(default=None),
    occurred_to: datetime.datetime | None = Query(default=None),
    entry_type: str | None = Query(default=None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> CustomerLedgerEntryListResponse:
    # Customer scope: verify representative owns this customer BEFORE
    # returning any ledger data.
    _require_customer_scope(customer_id, _current_user, db)
    items = customer_ledger_service.list_entries(
        db,
        customer_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        entry_type=entry_type,
        skip=skip,
        limit=limit,
    )
    return CustomerLedgerEntryListResponse(items=list(items))


@router.get(
    "/{customer_id}/balance",
    response_model=CustomerBalanceResponse,
    summary="Get customer's live balance (computed from entry log)",
)
def get_customer_balance(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_customer_ledger_view),
) -> CustomerBalanceResponse:
    # Customer scope: verify representative owns this customer BEFORE
    # returning any financial balance data.
    _require_customer_scope(customer_id, _current_user, db)
    balance = customer_ledger_service.get_balance(db, customer_id)
    return CustomerBalanceResponse(
        customer_id=customer_id,
        balance=balance,
    )


@router.post(
    "/{customer_id}/ledger/reconcile",
    response_model=CustomerLedgerReconcileResponse,
    summary="Reconcile the cached projection from the authoritative entry log",
)
def reconcile_customer_ledger(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_customer_ledger_manage),
) -> CustomerLedgerReconcileResponse:
    # Customer scope: verify representative owns this customer BEFORE
    # performing any financial reconciliation.
    _require_customer_scope(customer_id, _current_user, db)

    try:
        ledger = customer_ledger_service.reconcile_customer_ledger(db, customer_id)
    except customer_ledger_service.CustomerLedgerNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    db.refresh(ledger)
    return CustomerLedgerReconcileResponse.model_validate(ledger)


__all__ = ["router"]
