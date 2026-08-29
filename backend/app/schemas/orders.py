"""Request/response schemas for the Order endpoints (``/api/v1/orders``).

REWRITTEN -- the previous version modeled a different, simpler ``Order``
shape (integer PKs, a 5-value status, no ``representative_id``/
``currency_id``/``order_number``) that does not match the actual
``database/models/order.py`` (T10) ORM model or the accepted ADR-004
state machine (see ``services/order_service.py``'s module docstring).
This version is aligned field-for-field with that model and that state
machine.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class OrderType(str, Enum):
    LOCAL = "LOCAL"
    DIRECT = "DIRECT"


class FulfillmentMode(str, Enum):
    REP_LOCAL = "REP_LOCAL"
    FACTORY_DIRECT = "FACTORY_DIRECT"


class OrderState(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    RESERVED = "RESERVED"
    FULFILLING = "FULFILLING"
    SHIPPED = "SHIPPED"
    INVOICED = "INVOICED"
    PAID = "PAID"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    BACKORDERED = "BACKORDERED"
    PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED"
    RETURNED = "RETURNED"


class OrderLineCreateRequest(BaseModel):
    """One line of ``POST /orders``'s request body.

    ``price_history_id`` is optional.  When provided, it is used as an
    explicit price source (the caller resolves pricing).  When omitted,
    the service auto-resolves the current price from the order's
    price list via ``price_list_service.get_current_price()``.
    """

    product_id: uuid.UUID
    fulfillment_warehouse_id: uuid.UUID
    price_history_id: uuid.UUID | None = None
    qty_ordered: decimal.Decimal = Field(gt=0)
    fulfillment_mode: FulfillmentMode
    lot_id: uuid.UUID | None = None
    discount_id: uuid.UUID | None = None
    discount_value: decimal.Decimal = Field(default=decimal.Decimal("0"), ge=0)


class OrderLineAddRequest(BaseModel):
    """Request body for ``POST /orders/{id}/lines`` -- add a line to a DRAFT order.

    Reuses the same field set as ``OrderLineCreateRequest`` since the
    pricing resolution logic is identical.
    """

    product_id: uuid.UUID
    fulfillment_warehouse_id: uuid.UUID
    price_history_id: uuid.UUID | None = None
    qty_ordered: decimal.Decimal = Field(gt=0)
    fulfillment_mode: FulfillmentMode
    lot_id: uuid.UUID | None = None
    discount_id: uuid.UUID | None = None
    discount_value: decimal.Decimal = Field(default=decimal.Decimal("0"), ge=0)


class OrderLineUpdateQtyRequest(BaseModel):
    """Request body for ``PATCH /orders/{id}/lines/{line_id}`` --
    update quantity on a DRAFT order line.

    Unit price is frozen and NOT changed by this operation.
    """

    qty_ordered: decimal.Decimal = Field(gt=0)


class OrderLineUpdatePriceRequest(BaseModel):
    """Request body for ``PATCH /orders/{id}/lines/{line_id}/price`` --
    override the selling price on a DRAFT order line.

    Per ``04_Business_Policies.md``: *"Representative may change selling
    price.  Price change affects only current invoice."*  The override
    affects only the current DRAFT order; ``price_history_id`` provenance
    is preserved.
    """

    unit_price: decimal.Decimal = Field(ge=0)


class OrderLineApplyDiscountRequest(BaseModel):
    """Request body for ``PATCH /orders/{id}/lines/{line_id}/discount`` --
    apply an explicit discount to a DRAFT order line.

    BR-P2 Phase A: single explicit discount per line.  The caller
    provides a ``discount_id``; the system validates validity,
    applicability, and calculates the monetary discount value.
    """

    discount_id: uuid.UUID


class OrderLineRemoveDiscountRequest(BaseModel):
    """Request body for ``DELETE /orders/{id}/lines/{line_id}/discount`` --
    remove the discount from a DRAFT order line.
    """

    pass


class OrderCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "customer_id": "00000000-0000-0000-0000-000000000000",
                "representative_id": "00000000-0000-0000-0000-000000000000",
                "currency_id": "00000000-0000-0000-0000-000000000000",
                "price_list_id": "00000000-0000-0000-0000-000000000000",
                "order_type": "LOCAL",
                "fulfillment_mode": "REP_LOCAL",
                "sales_channel": "OFFICE",
                "lines": [
                    {
                        "product_id": "00000000-0000-0000-0000-000000000000",
                        "fulfillment_warehouse_id": "00000000-0000-0000-0000-000000000000",
                        "qty_ordered": "10",
                        "fulfillment_mode": "REP_LOCAL",
                    }
                ],
            }
        }
    )

    customer_id: uuid.UUID
    representative_id: uuid.UUID
    currency_id: uuid.UUID
    price_list_id: uuid.UUID | None = None
    order_type: OrderType
    fulfillment_mode: FulfillmentMode
    sales_channel: str = Field(min_length=1, max_length=24)
    lines: list[OrderLineCreateRequest] = Field(min_length=1)
    customer_city_ref_id: uuid.UUID | None = None
    rep_city_ref_id: uuid.UUID | None = None


class OrderTransitionRequest(BaseModel):
    """Generic body for the simple one-step transition endpoints
    (submit/approve/resubmit/cancel/start-fulfillment/return/complete).
    ``note`` is optional free text stored on the
    ``order_status_history`` row."""

    note: str | None = Field(default=None, max_length=2000)


class OrderPaymentRequest(BaseModel):
    """Request body for ``POST /orders/{id}/pay``.

    Records a real payment against the order's linked invoice,
    transitioning both the invoice and order to PAID.
    ``amount`` must be >= the invoice's ``balance_due`` to fully pay it.
    """

    amount: decimal.Decimal = Field(gt=0)
    method: str = Field(min_length=1, max_length=40, description="Payment method (e.g. CASH, BANK_TRANSFER)")
    reference: str | None = Field(default=None, max_length=200, description="Optional payment reference number")
    note: str | None = Field(default=None, max_length=2000)


class ShipmentLineRequest(BaseModel):
    order_line_id: uuid.UUID
    quantity: decimal.Decimal = Field(gt=0)


class ShipOrderRequest(BaseModel):
    lines: list[ShipmentLineRequest] = Field(min_length=1)


class OrderLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    lot_id: uuid.UUID | None
    fulfillment_warehouse_id: uuid.UUID
    qty_ordered: decimal.Decimal
    qty_reserved: decimal.Decimal
    qty_shipped: decimal.Decimal
    qty_returned: decimal.Decimal
    unit_price: decimal.Decimal
    discount_value: decimal.Decimal
    discount_id: uuid.UUID | None
    price_history_id: uuid.UUID
    line_total: decimal.Decimal
    fulfillment_mode: FulfillmentMode


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    customer_id: uuid.UUID
    representative_id: uuid.UUID
    sales_channel: str
    fulfillment_warehouse_id: uuid.UUID | None
    order_type: OrderType
    fulfillment_mode: FulfillmentMode
    state: OrderState
    currency_id: uuid.UUID
    price_list_id: uuid.UUID
    subtotal: decimal.Decimal
    discount_total: decimal.Decimal
    tax_total: decimal.Decimal
    grand_total: decimal.Decimal
    ordered_at: datetime.datetime
    shipped_at: datetime.datetime | None
    invoiced_at: datetime.datetime | None
    paid_at: datetime.datetime | None
    customer_city_ref_id: uuid.UUID | None
    rep_city_ref_id: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    lines: list[OrderLineResponse] = []


class OrderListResponse(BaseModel):
    items: list[OrderResponse]


class OrderLineListResponse(BaseModel):
    items: list[OrderLineResponse]


class OrderStatusHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    actor_user_id: uuid.UUID
    from_state: OrderState
    to_state: OrderState
    event_at: datetime.datetime
    note: str | None


class OrderHistoryResponse(BaseModel):
    items: list[OrderStatusHistoryResponse]


__all__ = [
    "FulfillmentMode",
    "OrderCreateRequest",
    "OrderHistoryResponse",
    "OrderLineAddRequest",
    "OrderLineApplyDiscountRequest",
    "OrderLineCreateRequest",
    "OrderLineListResponse",
    "OrderLineRemoveDiscountRequest",
    "OrderLineResponse",
    "OrderLineUpdatePriceRequest",
    "OrderLineUpdateQtyRequest",
    "OrderListResponse",
    "OrderPaymentRequest",
    "OrderResponse",
    "OrderState",
    "OrderStatusHistoryResponse",
    "OrderTransitionRequest",
    "OrderType",
    "ShipOrderRequest",
    "ShipmentLineRequest",
]
