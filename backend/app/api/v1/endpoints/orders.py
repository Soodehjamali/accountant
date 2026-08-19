"""Sales Order endpoints -- NOT WIRED INTO THE APP. See router.py.

STATUS NOTE (added during review): this module is intentionally left out
of ``app/api/v1/router.py``'s ``include_router`` calls. It cannot run as
written -- it imports ``app.db.session``, ``app.models.order``,
``app.models.customer``, ``app.models.product``, ``app.models.user``, and
``app.dependencies.rbac.PermissionChecker``, none of which exist in this
codebase (real paths: ``app.dependencies.db``, ``database.models.*``,
``app.dependencies.rbac.require_permission``). It also assumes integer
PKs and a 5-value order status (draft/pending/approved/cancelled/
completed), but the real ``database/models/order.py`` (T10) uses UUID
PKs, a required ``representative_id``, a required ``currency_id``, a
generated ``order_number``, ``sales_channel``/``fulfillment_mode``
fields, and a 13-value ``state`` enum with an application-level
transition graph that ``07_DATABASE_SPEC.md`` explicitly says is *not yet
specified* anywhere in the docs (only "guarded by an application-level
state machine" -- the graph itself is undocumented).

Per ``CLAUDE.md``'s own rule ("Never generate code before design
approval" / "No breaking changes without ADR"), this module should not be
rewritten and wired in until that state-machine design (and the
stock-reservation-before-DRAFT-exit rule referenced in the model's
docstring) is written down and approved -- the same way RBAC and
Inventory each got a real design pass before their services were built.
Recommended next step: write an ADR / extend ``02_SRS.md`` with the
Order state-transition table, then rebuild this module against
``services/order_service.py`` (not yet created) the way
``endpoints/rbac.py`` wraps ``services/rbac_service.py``.
"""

from typing import List, Optional
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.session import get_db
from app.models.order import Order, OrderItem, OrderStatusHistory
from app.models.customer import Customer
from app.models.product import Product
from app.models.user import User
from app.schemas.order import OrderCreate, OrderStatusUpdate, OrderResponse, OrderStatus
from app.dependencies.rbac import PermissionChecker
from app.dependencies.auth import get_current_user

router = APIRouter()


@router.post(
    "/",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(PermissionChecker("order:create"))],
)
def create_order(
    order_in: OrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """ثبت سفارش فروش جدید و ایجاد لاگ وضعیت اولیه"""
    # ۱. بررسی وجود و فعال بودن مشتری
    customer = db.get(Customer, order_in.customer_id)
    if not customer or not customer.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="مشتری یافت نشد یا حساب آن غیرفعال است.",
        )

    # ۲. محاسبه مجموع قیمت و اعتبارسنجی اقلام
    total_amount = Decimal("0.00")
    order_items = []

    for item in order_in.items:
        product = db.get(Product, item.product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"کالا با شناسه {item.product_id} یافت نشد.",
            )

        item_total = item.quantity * item.unit_price
        total_amount += item_total

        order_items.append(
            OrderItem(
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
                total_price=item_total,
            )
        )

    # ۳. ایجاد سفارش
    order = Order(
        customer_id=order_in.customer_id,
        total_amount=total_amount,
        status=OrderStatus.PENDING.value,
        note=order_in.note,
        items=order_items,
    )
    db.add(order)
    db.flush()  # دریافت شناسه سفارش پیش از ثبت کامل

    # ۴. ثبت تاریخچه وضعیت اولیه
    history = OrderStatusHistory(
        order_id=order.id,
        old_status=None,
        new_status=OrderStatus.PENDING.value,
        comment="ثبت اولیه سفارش در سیستم",
        created_by_id=current_user.id,
    )
    db.add(history)

    db.commit()
    db.refresh(order)
    return order


@router.get(
    "/",
    response_model=List[OrderResponse],
    dependencies=[Depends(PermissionChecker("order:read"))],
)
def read_orders(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    customer_id: Optional[int] = Query(None, description="فیلتر سفارشات بر اساس مشتری"),
    order_status: Optional[OrderStatus] = Query(None, alias="status", description="فیلتر بر اساس وضعیت سفارش"),
):
    """دریافت لیست سفارشات با قابلیت فیلتر بر اساس مشتری و وضعیت"""
    query = select(Order)

    if customer_id:
        query = query.where(Order.customer_id == customer_id)
    if order_status:
        query = query.where(Order.status == order_status.value)

    query = query.order_by(Order.created_at.desc()).offset(skip).limit(limit)
    orders = db.execute(query).scalars().all()
    return orders


@router.get(
    "/{order_id}",
    response_model=OrderResponse,
    dependencies=[Depends(PermissionChecker("order:read"))],
)
def read_order(
    order_id: int,
    db: Session = Depends(get_db),
):
    """مشاهده جزئیات سفارش و تاریخچه تغییر وضعیت آن"""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="سفارش مورد نظر یافت نشد.",
        )
    return order


@router.patch(
    "/{order_id}/status",
    response_model=OrderResponse,
    dependencies=[Depends(PermissionChecker("order:update"))],
)
def update_order_status(
    order_id: int,
    status_in: OrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """تغییر وضعیت سفارش و ثبت خودکار در تاریخچه وضعیت (Order Status History)"""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="سفارش مورد نظر یافت نشد.",
        )

    if order.status == status_in.status.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="سفارش در حال حاضر در همین وضعیت قرار دارد.",
        )

    old_status = order.status
    order.status = status_in.status.value

    history = OrderStatusHistory(
        order_id=order.id,
        old_status=old_status,
        new_status=status_in.status.value,
        comment=status_in.comment,
        created_by_id=current_user.id,
    )
    
    db.add(order)
    db.add(history)
    db.commit()
    db.refresh(order)
    return order