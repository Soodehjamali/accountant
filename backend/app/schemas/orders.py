from decimal import Decimal
from enum import Enum
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class OrderStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


# --- Order Item Schemas ---
class OrderItemBase(BaseModel):
    product_id: int = Field(..., description="شناسه کالا")
    quantity: int = Field(..., gt=0, description="تعداد یا مقدار سفارش")
    unit_price: Decimal = Field(..., ge=0, description="قیمت واحد")


class OrderItemCreate(OrderItemBase):
    pass


class OrderItemResponse(OrderItemBase):
    id: int
    order_id: int
    total_price: Decimal

    model_config = ConfigDict(from_attributes=True)


# --- Order History Schema ---
class OrderHistoryResponse(BaseModel):
    id: int
    order_id: int
    old_status: Optional[str] = None
    new_status: str
    comment: Optional[str] = None
    created_at: datetime
    created_by_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


# --- Order Schemas ---
class OrderBase(BaseModel):
    customer_id: int = Field(..., description="شناسه مشتری")
    note: Optional[str] = Field(None, max_length=500, description="یادداشت یا توضیحات سفارش")


class OrderCreate(OrderBase):
    items: List[OrderItemCreate] = Field(..., min_length=1, description="حداقل یک آیتم برای ثبت سفارش الزامی است")


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    comment: Optional[str] = Field(None, max_length=255, description="علت یا توضیحات تغییر وضعیت")


class OrderResponse(OrderBase):
    id: int
    status: OrderStatus
    total_amount: Decimal
    created_at: datetime
    updated_at: Optional[datetime] = None
    items: List[OrderItemResponse] = []
    status_history: List[OrderHistoryResponse] = []

    model_config = ConfigDict(from_attributes=True)