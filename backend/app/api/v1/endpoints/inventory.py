"""Inventory ledger endpoints: ``POST /inventory/transactions``,
``GET /inventory/balance``, ``POST /inventory/transactions/{id}/reverse``.

Thin HTTP wrappers around ``services.inventory_service`` -- per this
project's layering rule, every invariant (sign-matching, no-negative-stock,
hash-chain) lives there, not here. See that module's docstring for why it
is the sole sanctioned write path onto ``inventory_transaction`` -- this
router is the only caller of it from the API surface, and must stay that
way (no endpoint here or elsewhere should construct
``InventoryTransaction`` rows directly).

All endpoints require an authenticated caller. As with
``app/api/v1/endpoints/products.py``, there is no RBAC/permission system
yet -- narrowing "who may post ledger entries" to specific roles is a
later task once RBAC is wired up.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.schemas.inventory import (
    BalanceResponse,
    PostTransactionRequest,
    ReverseTransactionRequest,
    TransactionResponse,
)
from database.models.app_user import AppUser
from services import inventory_service

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.post(
    "/transactions",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post an inventory ledger transaction",
)
def post_transaction(
    body: PostTransactionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> TransactionResponse:
    """Append one immutable row to the inventory ledger.

    Returns HTTP 422 for an unknown movement type or a sign mismatch
    between ``signed_quantity`` and the movement type's sign convention,
    and HTTP 409 if posting would drive the balance negative -- 409
    (Conflict) because the request is well-formed but conflicts with
    current ledger state, the same reasoning ``products.py`` uses for a
    duplicate SKU.
    """

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
    current_user: AppUser = Depends(get_current_user),
) -> TransactionResponse:
    """Post a new REVERSAL row exactly negating ``transaction_id``.

    Never mutates or deletes the original row (append-only ledger). 404 if
    the id doesn't exist; 409 if it was already reversed, or if the
    reversal itself would drive the balance negative.
    """

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
