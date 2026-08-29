"""Sales Order endpoints: ``/api/v1/orders``.

Every mutating endpoint is gated behind ``ORDER_MANAGE`` via
``require_permission``, except the approval step itself, which is gated
behind the separate ``ORDER_APPROVE`` permission.

Representative scope:
All order-by-id endpoints (read and write) enforce representative scope
via ``require_order_scope``.  Representative-linked users can only
access orders belonging to their representative.  Admin/staff users with
no representative link can access any order.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_order_scope, require_permission
from app.schemas.orders import (
    OrderCreateRequest,
    OrderHistoryResponse,
    OrderLineListResponse,
    OrderLineResponse,
    OrderListResponse,
    OrderResponse,
    OrderStatusHistoryResponse,
    OrderTransitionRequest,
    ShipOrderRequest,
)
from database.models.app_user import AppUser
from database.models.order import Order
from services import order_service

router = APIRouter(prefix="/orders", tags=["orders"])

_require_order_manage = require_permission(order_service.ORDER_MANAGE_PERMISSION_CODE)
_require_order_approve = require_permission(order_service.ORDER_APPROVE_PERMISSION_CODE)

#: Every exception order_service's transition functions can raise, mapped
#: to the HTTP status it should surface as.
_ERROR_STATUS_MAP: tuple[tuple[type[Exception], int], ...] = (
    (order_service.OrderNotFoundError, status.HTTP_404_NOT_FOUND),
    (order_service.OrderNotCancellableError, status.HTTP_409_CONFLICT),
    (order_service.InvalidOrderStateTransitionError, status.HTTP_409_CONFLICT),
    (order_service.OrderLineNotFoundError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (order_service.ShipmentQuantityError, status.HTTP_422_UNPROCESSABLE_ENTITY),
)


def _run(func, /, *args, **kwargs):
    """Call an order_service function, translating its documented
    exceptions into the matching HTTPException via ``_ERROR_STATUS_MAP``."""

    try:
        return func(*args, **kwargs)
    except Exception as exc:
        for exc_type, http_status in _ERROR_STATUS_MAP:
            if isinstance(exc, exc_type):
                raise HTTPException(http_status, detail=str(exc)) from exc
        raise


def _to_response(order, lines=None) -> OrderResponse:
    response = OrderResponse.model_validate(order)
    if lines is not None:
        response.lines = [OrderLineResponse.model_validate(line) for line in lines]
    return response


def _line_input_from_request(line) -> order_service.OrderLineInput:
    return order_service.OrderLineInput(
        product_id=line.product_id,
        fulfillment_warehouse_id=line.fulfillment_warehouse_id,
        price_history_id=line.price_history_id,
        qty_ordered=line.qty_ordered,
        fulfillment_mode=line.fulfillment_mode.value,
        lot_id=line.lot_id,
        discount_id=line.discount_id,
        discount_value=line.discount_value,
    )


# -----------------------------------------------------------------------
# Create
# -----------------------------------------------------------------------

@router.post(
    "", response_model=OrderResponse, status_code=status.HTTP_201_CREATED, summary="Create a draft order"
)
def create_order(
    body: OrderCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
) -> OrderResponse:
    # Scope: representative-linked users may only create orders for
    # their own representative.  Admin/staff users (no representative
    # link) may create orders for any representative.
    if (
        current_user.representative_id is not None
        and body.representative_id != current_user.representative_id
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Cannot create orders for a different representative.",
        )
    try:
        order = order_service.create_order(
            db,
            customer_id=body.customer_id,
            representative_id=body.representative_id,
            currency_id=body.currency_id,
            order_type=body.order_type.value,
            fulfillment_mode=body.fulfillment_mode.value,
            sales_channel=body.sales_channel,
            lines=[_line_input_from_request(line) for line in body.lines],
            created_by=current_user.id,
            customer_city_ref_id=body.customer_city_ref_id,
            rep_city_ref_id=body.rep_city_ref_id,
        )
    except (
        order_service.CustomerNotFoundError,
        order_service.RepresentativeNotFoundError,
        order_service.ProductNotFoundError,
    ) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (order_service.EmptyOrderError, order_service.PriceHistoryMismatchError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    db.refresh(order)
    lines = order_service.list_order_lines(db, order.id)
    return _to_response(order, lines)


# -----------------------------------------------------------------------
# Read
# -----------------------------------------------------------------------

@router.get("", response_model=OrderListResponse, summary="List orders")
def list_orders(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    customer_id: uuid.UUID | None = Query(default=None),
    representative_id: uuid.UUID | None = Query(default=None),
    state: str | None = Query(default=None),
) -> OrderListResponse:
    # Server-side representative scope: representative-linked users
    # can only see their own orders, regardless of any client-supplied
    # representative_id parameter.  Admin/staff users (no representative
    # link) retain the existing optional-filter behavior.
    if current_user.representative_id is not None:
        representative_id = current_user.representative_id
    orders = order_service.list_orders(
        db,
        customer_id=customer_id,
        representative_id=representative_id,
        state=state,
        skip=skip,
        limit=limit,
    )
    return OrderListResponse(items=[_to_response(order) for order in orders])


@router.get("/{order_id}", response_model=OrderResponse, summary="Get an order and its lines")
def read_order(
    order: Order = Depends(require_order_scope),
    db: Session = Depends(get_db),
) -> OrderResponse:
    lines = order_service.list_order_lines(db, order.id)
    return _to_response(order, lines)


@router.get("/{order_id}/lines", response_model=OrderLineListResponse, summary="List an order's lines")
def read_order_lines(
    order: Order = Depends(require_order_scope),
    db: Session = Depends(get_db),
) -> OrderLineListResponse:
    lines = order_service.list_order_lines(db, order.id)
    return OrderLineListResponse(items=[OrderLineResponse.model_validate(line) for line in lines])


@router.get(
    "/{order_id}/history",
    response_model=OrderHistoryResponse,
    summary="Get an order's state-transition history",
)
def read_order_history(
    order: Order = Depends(require_order_scope),
    db: Session = Depends(get_db),
) -> OrderHistoryResponse:
    history = order_service.get_order_history(db, order.id)
    return OrderHistoryResponse(items=[OrderStatusHistoryResponse.model_validate(h) for h in history])


# -----------------------------------------------------------------------
# State transitions (all require ORDER_MANAGE + representative scope)
# -----------------------------------------------------------------------

@router.post("/{order_id}/submit", response_model=OrderResponse, summary="DRAFT -> PENDING_APPROVAL")
def submit_order(
    body: OrderTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    order = _run(order_service.submit_order, db, order.id, actor_user_id=current_user.id, note=body.note)
    db.commit()
    db.refresh(order)
    return _to_response(order)


@router.post("/{order_id}/approve", response_model=OrderResponse, summary="PENDING_APPROVAL -> APPROVED")
def approve_order(
    body: OrderTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_approve),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    order = _run(order_service.approve_order, db, order.id, actor_user_id=current_user.id, note=body.note)
    db.commit()
    db.refresh(order)
    return _to_response(order)


@router.post(
    "/{order_id}/reserve",
    response_model=OrderResponse,
    summary="APPROVED -> RESERVED (or BACKORDERED if stock is insufficient)",
)
def reserve_order(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    order = _run(order_service.reserve_order_stock, db, order.id, actor_user_id=current_user.id)
    db.commit()
    db.refresh(order)
    return _to_response(order)


@router.post("/{order_id}/resubmit", response_model=OrderResponse, summary="BACKORDERED -> PENDING_APPROVAL")
def resubmit_order(
    body: OrderTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    order = _run(order_service.resubmit_order, db, order.id, actor_user_id=current_user.id, note=body.note)
    db.commit()
    db.refresh(order)
    return _to_response(order)


@router.post("/{order_id}/cancel", response_model=OrderResponse, summary="-> CANCELLED (any state before SHIPPED)")
def cancel_order(
    body: OrderTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    order = _run(order_service.cancel_order, db, order.id, actor_user_id=current_user.id, note=body.note)
    db.commit()
    db.refresh(order)
    return _to_response(order)


@router.post("/{order_id}/start-fulfillment", response_model=OrderResponse, summary="RESERVED -> FULFILLING")
def start_fulfillment(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    order = _run(order_service.start_fulfillment, db, order.id, actor_user_id=current_user.id)
    db.commit()
    db.refresh(order)
    return _to_response(order)


@router.post(
    "/{order_id}/ship",
    response_model=OrderResponse,
    summary="Record a shipment -> SHIPPED (or PARTIALLY_FULFILLED)",
)
def ship_order(
    body: ShipOrderRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    shipments = [
        order_service.ShipmentInput(order_line_id=line.order_line_id, quantity=line.quantity)
        for line in body.lines
    ]
    order = _run(
        order_service.ship_order, db, order.id, shipments=shipments, actor_user_id=current_user.id
    )
    db.commit()
    db.refresh(order)
    return _to_response(order)


@router.post("/{order_id}/return", response_model=OrderResponse, summary="SHIPPED/PARTIALLY_FULFILLED -> RETURNED")
def record_return(
    body: OrderTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    order = _run(order_service.record_return, db, order.id, actor_user_id=current_user.id, note=body.note)
    db.commit()
    db.refresh(order)
    return _to_response(order)


@router.post("/{order_id}/invoice", response_model=OrderResponse, summary="SHIPPED -> INVOICED")
def mark_invoiced(
    body: OrderTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    order = _run(order_service.mark_invoiced, db, order.id, actor_user_id=current_user.id, note=body.note)
    db.commit()
    db.refresh(order)
    return _to_response(order)


@router.post("/{order_id}/pay", response_model=OrderResponse, summary="INVOICED -> PAID")
def mark_paid(
    body: OrderTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    order = _run(order_service.mark_paid, db, order.id, actor_user_id=current_user.id, note=body.note)
    db.commit()
    db.refresh(order)
    return _to_response(order)


@router.post("/{order_id}/complete", response_model=OrderResponse, summary="PAID -> COMPLETED")
def mark_completed(
    body: OrderTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    order = _run(order_service.mark_completed, db, order.id, actor_user_id=current_user.id, note=body.note)
    db.commit()
    db.refresh(order)
    return _to_response(order)


__all__ = ["router"]
