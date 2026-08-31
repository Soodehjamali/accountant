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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import _require_customer_scope, require_order_scope, require_permission
from app.schemas.orders import (
    OrderCreateRequest,
    OrderHistoryResponse,
    OrderLineAddRequest,
    OrderLineApplyDiscountRequest,
    OrderLineListResponse,
    OrderLineRemoveDiscountRequest,
    OrderLineResponse,
    OrderLineUpdatePriceRequest,
    OrderLineUpdateQtyRequest,
    OrderListResponse,
    OrderPaymentRequest,
    OrderResponse,
    OrderStatusHistoryResponse,
    OrderTransitionRequest,
    ShipOrderRequest,
)
from database.models.app_user import AppUser
from database.models.invoice_order import InvoiceOrder
from database.models.order import Order
from services import customer_ledger_service, invoice_service, order_service, payment_service

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
    (order_service.CustomerCreditLimitExceededError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (order_service.ShipmentQuantityError, status.HTTP_422_UNPROCESSABLE_ENTITY),
)

#: Exceptions from invoice_service / payment_service that can surface
#: during the integrated invoice/payment endpoints.
_INVOICE_PAYMENT_ERROR_MAP: tuple[tuple[type[Exception], int], ...] = (
    (invoice_service.InvoiceNotFoundError, status.HTTP_404_NOT_FOUND),
    (invoice_service.OrderNotFoundError, status.HTTP_404_NOT_FOUND),
    (invoice_service.OrderNotShippedError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (invoice_service.InvalidInvoiceStateTransitionError, status.HTTP_409_CONFLICT),
    (invoice_service.OrderNotInShippableStateForInvoiceError, status.HTTP_409_CONFLICT),
    (invoice_service.VoidOnlyFromDraftError, status.HTTP_409_CONFLICT),
    (invoice_service.PaymentExceedsBalanceError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (payment_service.PaymentExceedsTotalAllocationsError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (payment_service.InvoiceAllocationExceedsBalanceError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (payment_service.InvoiceNotPayableError, status.HTTP_409_CONFLICT),
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


def _run_invoice_payment(func, /, *args, **kwargs):
    """Call an invoice/payment service function, translating exceptions
    into the matching HTTPException via ``_INVOICE_PAYMENT_ERROR_MAP``."""

    try:
        return func(*args, **kwargs)
    except Exception as exc:
        for exc_type, http_status in _INVOICE_PAYMENT_ERROR_MAP:
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


# --- Error types added by pricing integration ---
_PRICE_ERROR_MAP: tuple[tuple[type[Exception], int], ...] = (
    (order_service.PriceListNotFoundError, status.HTTP_400_BAD_REQUEST),
    (order_service.PriceListNotActiveError, status.HTTP_409_CONFLICT),
    (order_service.NoCurrentPriceError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (order_service.NoCustomerPriceListError, status.HTTP_422_UNPROCESSABLE_ENTITY),
)

# --- Error types for discount application (BR-P2 Phase A) ---
_DISCOUNT_ERROR_MAP: tuple[tuple[type[Exception], int], ...] = (
    (order_service.OrderNotEditableError, status.HTTP_409_CONFLICT),
    (order_service.OrderLineNotFoundError, status.HTTP_422_UNPROCESSABLE_ENTITY),
)

# Import discount_service exceptions for the error map.
from services import discount_service as _discount_svc  # noqa: E402

_DISCOUNT_SERVICE_ERROR_MAP: tuple[tuple[type[Exception], int], ...] = (
    (_discount_svc.DiscountNotFoundError, status.HTTP_404_NOT_FOUND),
    (_discount_svc.DiscountExpiredError, status.HTTP_409_CONFLICT),
    (_discount_svc.DiscountNotYetValidError, status.HTTP_409_CONFLICT),
    (_discount_svc.DiscountExceedsLineTotalError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (_discount_svc.DiscountProductMismatchError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (_discount_svc.DiscountCategoryMismatchError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (_discount_svc.DiscountCustomerMismatchError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (_discount_svc.DiscountRepresentativeMismatchError, status.HTTP_422_UNPROCESSABLE_ENTITY),
)

# --- Error types for order line editing ---
_EDIT_ERROR_MAP: tuple[tuple[type[Exception], int], ...] = (
    (order_service.OrderNotEditableError, status.HTTP_409_CONFLICT),
    (order_service.DuplicateProductOnOrderError, status.HTTP_409_CONFLICT),
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
    # Customer scope: representative-linked users may only create orders
    # for customers within their authorized scope (active assignment).
    _require_customer_scope(body.customer_id, current_user, db)
    try:
        order = order_service.create_order(
            db,
            customer_id=body.customer_id,
            representative_id=body.representative_id,
            currency_id=body.currency_id,
            price_list_id=body.price_list_id,
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
        order_service.PriceListNotFoundError,
    ) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (
        order_service.EmptyOrderError,
        order_service.PriceHistoryMismatchError,
        order_service.NoCurrentPriceError,
        order_service.NoCustomerPriceListError,
        order_service.CustomerCreditLimitExceededError,
    ) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except order_service.PriceListNotActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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


# -----------------------------------------------------------------------
# DRAFT order line editing
# -----------------------------------------------------------------------

@router.post(
    "/{order_id}/lines",
    response_model=OrderLineResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a line to a DRAFT order",
)
def add_order_line(
    order_id: uuid.UUID,
    body: OrderLineAddRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderLineResponse:
    line_input = order_service.OrderLineInput(
        product_id=body.product_id,
        fulfillment_warehouse_id=body.fulfillment_warehouse_id,
        price_history_id=body.price_history_id,
        qty_ordered=body.qty_ordered,
        fulfillment_mode=body.fulfillment_mode.value,
        lot_id=body.lot_id,
        discount_id=body.discount_id,
        discount_value=body.discount_value,
    )
    try:
        line = order_service.add_order_line(
            db, order.id, line_input, actor_user_id=current_user.id,
        )
    except order_service.OrderNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (
        order_service.OrderNotEditableError,
        order_service.DuplicateProductOnOrderError,
    ) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (
        order_service.ProductNotFoundError,
        order_service.PriceListNotFoundError,
    ) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (
        order_service.PriceHistoryMismatchError,
        order_service.NoCurrentPriceError,
    ) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    db.refresh(line)
    return OrderLineResponse.model_validate(line)


@router.delete(
    "/{order_id}/lines/{line_id}",
    status_code=status.HTTP_200_OK,
    summary="Remove a line from a DRAFT order",
)
def remove_order_line(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> None:
    try:
        order_service.remove_order_line(
            db, order.id, line_id, actor_user_id=current_user.id,
        )
    except order_service.OrderNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except order_service.OrderNotEditableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except order_service.OrderLineNotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()


@router.patch(
    "/{order_id}/lines/{line_id}",
    response_model=OrderLineResponse,
    summary="Update quantity on a DRAFT order line",
)
def update_order_line_qty(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    body: OrderLineUpdateQtyRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderLineResponse:
    try:
        line = order_service.update_order_line_qty(
            db, order.id, line_id, body.qty_ordered, actor_user_id=current_user.id,
        )
    except order_service.OrderNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except order_service.OrderNotEditableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except order_service.OrderLineNotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    db.refresh(line)
    return OrderLineResponse.model_validate(line)


@router.patch(
    "/{order_id}/lines/{line_id}/price",
    response_model=OrderLineResponse,
    summary="Override price on a DRAFT order line",
)
def update_order_line_price(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    body: OrderLineUpdatePriceRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderLineResponse:
    """Override the selling price on a DRAFT order line.

    Per ``04_Business_Policies.md``: price change affects only the
    current invoice (DRAFT order).  The ``price_history_id`` provenance
    is preserved; only ``unit_price``, ``line_total``, and order totals
    are recalculated.
    """
    try:
        line = order_service.update_order_line_price(
            db, order.id, line_id, body.unit_price, actor_user_id=current_user.id,
        )
    except order_service.OrderNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except order_service.OrderNotEditableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except order_service.OrderLineNotFoundError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    db.refresh(line)
    return OrderLineResponse.model_validate(line)


# -----------------------------------------------------------------------
# DRAFT order line discount (BR-P2 Phase A)
# -----------------------------------------------------------------------


@router.patch(
    "/{order_id}/lines/{line_id}/discount",
    response_model=OrderLineResponse,
    summary="Apply an explicit discount to a DRAFT order line",
)
def apply_discount(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    body: OrderLineApplyDiscountRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderLineResponse:
    """Apply an explicit discount to a DRAFT order line.

    BR-P2 Phase A: single explicit discount per line.  The caller
    provides a ``discount_id``; the system validates validity,
    applicability (product/category/customer/representative scope),
    calculates the monetary value, and stores it on the line.

    Reuses ``ORDER_MANAGE`` permission (same as other DRAFT line edits).
    """
    try:
        line = order_service.apply_discount_to_order_line(
            db, order.id, line_id, body.discount_id,
            actor_user_id=current_user.id,
        )
    except order_service.OrderNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (order_service.OrderNotEditableError, order_service.OrderLineNotFoundError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        for exc_type, http_status in _DISCOUNT_SERVICE_ERROR_MAP:
            if isinstance(exc, exc_type):
                raise HTTPException(http_status, detail=str(exc)) from exc
        raise
    db.commit()
    db.refresh(line)
    return OrderLineResponse.model_validate(line)


@router.delete(
    "/{order_id}/lines/{line_id}/discount",
    response_model=OrderLineResponse,
    summary="Remove the discount from a DRAFT order line",
)
def remove_discount(
    order_id: uuid.UUID,
    line_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderLineResponse:
    """Remove the discount from a DRAFT order line.

    Resets ``discount_id`` to NULL and ``discount_value`` to 0,
    recalculates ``line_total`` and order totals.
    """
    try:
        line = order_service.remove_discount_from_order_line(
            db, order.id, line_id, actor_user_id=current_user.id,
        )
    except order_service.OrderNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (order_service.OrderNotEditableError, order_service.OrderLineNotFoundError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(line)
    return OrderLineResponse.model_validate(line)


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


@router.post(
    "/{order_id}/invoice", response_model=OrderResponse,
    summary="SHIPPED -> INVOICED (creates + issues a real invoice)",
)
def mark_invoiced(
    body: OrderTransitionRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    """Create an invoice from a shipped order, issue it, and transition
    the order to INVOICED — all atomically in one session.

    1. Creates a DRAFT invoice via ``invoice_service.create_invoice_from_order``.
    2. Issues the invoice (DRAFT -> ISSUED) via ``invoice_service.issue_invoice``,
       which also coordinates the order SHIPPED -> INVOICED transition and
       posts a customer ledger entry.
    """
    # Step 1: Create a DRAFT invoice from the shipped order.
    invoice = _run_invoice_payment(
        invoice_service.create_invoice_from_order,
        db, order_id=order.id, created_by=current_user.id,
        note=body.note,
    )
    # Step 2: Issue the invoice (DRAFT -> ISSUED).
    # issue_invoice() internally calls order_service.mark_invoiced()
    # and posts a customer ledger entry.
    _run_invoice_payment(
        invoice_service.issue_invoice,
        db, invoice.id,
        actor_user_id=current_user.id,
        note=body.note,
        record_entry=customer_ledger_service.record_entry,
    )
    db.commit()
    # Refresh the order — issue_invoice transitioned it to INVOICED.
    db.refresh(order)
    return _to_response(order)


@router.post(
    "/{order_id}/pay", response_model=OrderResponse,
    summary="INVOICED -> PAID (records a real payment against the linked invoice)",
)
def mark_paid(
    body: OrderPaymentRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_order_manage),
    order: Order = Depends(require_order_scope),
) -> OrderResponse:
    """Record a payment against the order's linked invoice, transitioning
    both the invoice and order to PAID — all atomically in one session.

    1. Finds the invoice linked to this order via ``invoice_order`` (J1).
    2. Records the payment via ``payment_service.record_payment``, which
       updates the invoice's ``amount_paid``/``balance_due`` and posts a
       customer ledger entry.
    3. Re-checks the invoice's ``balance_due``.  Only transitions the
       order INVOICED -> PAID if the invoice is fully settled
       (``balance_due == 0``).  Partial payments are recorded but the
       order stays INVOICED — the caller receives a 409 indicating the
       remaining balance.

    ``payment_service.record_payment`` only rejects amounts that
    *exceed* ``balance_due``; it happily accepts under-payments.
    This endpoint must therefore guard the order transition itself,
    not rely on the payment service to enforce full payment.
    """
    # Find the linked invoice.
    invoice_link = db.execute(
        select(InvoiceOrder).where(InvoiceOrder.order_id == order.id)
    ).scalar_one_or_none()
    if invoice_link is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"No invoice exists for order '{order.id}'. "
                   "Create an invoice first via POST /orders/{id}/invoice.",
        )

    # Record a payment against the invoice.
    _run_invoice_payment(
        payment_service.record_payment,
        db,
        customer_id=order.customer_id,
        currency_id=order.currency_id,
        amount=body.amount,
        method=body.method,
        allocations=[(invoice_link.invoice_id, body.amount)],
        actor_user_id=current_user.id,
        reference=body.reference,
        record_entry=customer_ledger_service.record_entry,
    )

    # Re-check the invoice's balance_due after the payment.
    # record_payment only rejects overpayments (amount > balance_due);
    # it happily accepts partial payments.  We must guard the order
    # transition here: only mark PAID if the invoice is fully settled.
    from database.models.invoice import Invoice  # local to avoid circular

    invoice = db.get(Invoice, invoice_link.invoice_id)
    if invoice is None:
        # Defensive — the invoice was just used by record_payment.
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Linked invoice not found after recording payment.",
        )
    db.refresh(invoice)  # ensure balance_due is current after record_payment

    if invoice.balance_due > 0:
        # Payment was recorded successfully, but the invoice is not
        # fully settled.  Do NOT transition the order to PAID —
        # commit the payment and return 409 so the caller knows the
        # order is still INVOICED with a remaining balance.
        db.commit()
        db.refresh(order)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Payment of {body.amount} recorded, but invoice still has "
                f"a balance of {invoice.balance_due} remaining — "
                f"order was not marked PAID."
            ),
        )

    # Invoice fully settled — transition the order INVOICED -> PAID.
    _run(order_service.mark_paid, db, order.id, actor_user_id=current_user.id, note=body.note)
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
