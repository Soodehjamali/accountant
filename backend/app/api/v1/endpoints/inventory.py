"""Inventory ledger endpoints: ``GET /inventory/transactions``,
``POST /inventory/transactions``, ``GET /inventory/balance``,
``POST /inventory/transactions/{id}/reverse``.

Thin HTTP wrappers around ``services.inventory_service`` -- per this
project's layering rule, every invariant (sign-matching, no-negative-stock,
hash-chain) lives there, not here. See that module's docstring for why it
is the sole sanctioned write path onto ``inventory_transaction`` -- this
router is the only caller of it from the API surface, and must stay that
way (no endpoint here or elsewhere should construct
``InventoryTransaction`` rows directly).

Mutation endpoints (POST transactions, POST reverse) require the
``INVENTORY_MANAGE`` permission via ``require_permission``.  Read
endpoints (GET transactions, GET balance) require only an authenticated
caller with warehouse scope.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import _require_warehouse_scope, require_permission
from app.schemas.inventory import (
    BalanceResponse,
    PostTransactionRequest,
    ReverseTransactionRequest,
    TransactionListResponse,
    TransactionResponse,
)
from database.models.app_user import AppUser
from database.models.inventory_transaction import InventoryTransaction
from services import inventory_service

router = APIRouter(prefix="/inventory", tags=["inventory"])

INVENTORY_MANAGE_PERMISSION_CODE = "INVENTORY_MANAGE"
_require_inventory_manage = require_permission(INVENTORY_MANAGE_PERMISSION_CODE)


@router.get(
    "/transactions",
    response_model=TransactionListResponse,
    summary="List inventory ledger transactions",
)
def list_transactions(
    warehouse_id: uuid.UUID,
    product_id: uuid.UUID | None = Query(default=None),
    lot_id: uuid.UUID | None = Query(default=None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> TransactionListResponse:
    """Return raw ledger rows for a warehouse, optionally filtered by
    product and/or lot.  Ordered by ``sequence_no`` DESC (newest first).

    Read access only -- no ``INVENTORY_MANAGE`` required, matching
    ``GET /inventory/balance``'s own convention.
    """
    _require_warehouse_scope(warehouse_id, _current_user, db)

    query = select(InventoryTransaction).where(
        InventoryTransaction.warehouse_id == warehouse_id
    )
    if product_id is not None:
        query = query.where(InventoryTransaction.product_id == product_id)
    if lot_id is not None:
        query = query.where(InventoryTransaction.lot_id == lot_id)
    query = query.order_by(InventoryTransaction.sequence_no.desc())
    query = query.offset(skip).limit(limit)
    rows = db.execute(query).scalars().all()
    return TransactionListResponse(
        items=[TransactionResponse.model_validate(r) for r in rows]
    )


@router.post(
    "/transactions",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post an inventory ledger transaction",
)
def post_transaction(
    body: PostTransactionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_inventory_manage),
) -> TransactionResponse:
    """Append one immutable row to the inventory ledger.

    Returns HTTP 422 for an unknown movement type or a sign mismatch
    between ``signed_quantity`` and the movement type's sign convention,
    and HTTP 409 if posting would drive the balance negative -- 409
    (Conflict) because the request is well-formed but conflicts with
    current ledger state, the same reasoning ``products.py`` uses for a
    duplicate SKU.
    """

    # Warehouse scope: verify the representative has authorization for
    # the target warehouse before posting any ledger transaction.
    _require_warehouse_scope(body.warehouse_id, current_user, db)

    try:
        transaction = inventory_service.post_transaction(
            db,
            product_id=body.product_id,
            warehouse_id=body.warehouse_id,
            movement_type_code=body.movement_type_code,
            signed_quantity=body.signed_quantity,
            unit_cost=body.unit_cost,
            currency_id=body.currency_id,
            actor_user_id=current_user.id,
            lot_id=body.lot_id,
            reason_code_id=body.reason_code_id,
            reference_type=body.reference_type,
            reference_id=body.reference_id,
        )
    except (
        inventory_service.UnknownMovementTypeError,
        inventory_service.MovementTypeSignMismatchError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except inventory_service.NegativeStockError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(transaction)
    return transaction


@router.post(
    "/transactions/{transaction_id}/reverse",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reverse a previously posted ledger transaction",
)
def reverse_transaction(
    transaction_id: uuid.UUID,
    body: ReverseTransactionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_inventory_manage),
) -> TransactionResponse:
    """Post a new REVERSAL row exactly negating ``transaction_id``.

    Never mutates or deletes the original row (append-only ledger). 404 if
    the id doesn't exist; 409 if it was already reversed, or if the
    reversal itself would drive the balance negative.
    """

    # Warehouse scope: load the transaction to determine its warehouse,
    # then verify the representative has authorization for that warehouse
    # BEFORE invoking the reverse service.
    from sqlalchemy import select as _sel
    from database.models.inventory_transaction import InventoryTransaction as _IT

    _txn = db.execute(
        _sel(_IT).where(_IT.id == transaction_id)
    ).scalar_one_or_none()
    if _txn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found.",
        )
    _require_warehouse_scope(_txn.warehouse_id, current_user, db)

    try:
        reversal = inventory_service.reverse_transaction(
            db,
            transaction_id,
            actor_user_id=current_user.id,
            reason_code_id=body.reason_code_id,
        )
    except inventory_service.TransactionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (
        inventory_service.AlreadyReversedError,
        inventory_service.NegativeStockError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(reversal)
    return reversal


@router.get("/balance", response_model=BalanceResponse, summary="Get the current balance")
def get_balance(
    warehouse_id: uuid.UUID,
    product_id: uuid.UUID,
    lot_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(get_current_user),
) -> BalanceResponse:
    """Return the current projected balance for (warehouse, product, lot).

    Always computed live from the ledger (per ``CLAUDE.md``: "Inventory is
    always calculated from immutable InventoryTransaction") -- never a
    cached column.
    """

    # Warehouse scope: verify the representative has authorization for
    # the queried warehouse before reading balances.
    _require_warehouse_scope(warehouse_id, _current_user, db)

    balance = inventory_service.get_balance(
        db, warehouse_id=warehouse_id, product_id=product_id, lot_id=lot_id
    )
    return BalanceResponse(
        warehouse_id=warehouse_id,
        product_id=product_id,
        lot_id=lot_id,
        balance=balance,
    )


__all__ = ["router"]
