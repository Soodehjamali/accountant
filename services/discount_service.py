"""``discount_service`` — canonical discount resolution and calculation.

BR-P2 Phase A: single explicit discount per order line.

This service owns:
* discount existence and validity checking
* applicability validation (product/category/customer/representative scope)
* discount monetary calculation (PERCENT and AMOUNT)
* negative line total prevention

The service is called by ``order_service`` when a discount is applied to
an order line.  API and Bot layers must NOT duplicate discount logic.

Per ``02_SRS.md`` BR-P2: *"Discounts apply within authorization limits."*
Authorization limits are DEFERRED (no business rule defines them yet).
Phase A implements only basic single-discount application.

Per ``06_ERD.md`` H3:
* DiscountType: PERCENT, AMOUNT
* scope fields: product_id, category_id, customer_id, representative_id
  (all nullable — NULL means "not scoped to that dimension")
* valid_from / valid_to: time-bounded validity window
* "promo-style discounts never applied retroactively"

Calculation:
* PERCENT: discount_value = gross_line_amount × (value / 100)
  where gross_line_amount = unit_price × qty
* AMOUNT: discount_value = value (fixed currency amount)

The resulting line_total = gross_line_amount − discount_value must never
be negative.  If the configured discount exceeds the line gross amount,
the operation is REJECTED (not clamped to zero).
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.discount import Discount
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.product import Product


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DiscountNotFoundError(LookupError):
    """Raised when a referenced ``discount_id`` has no matching row."""

    def __init__(self, discount_id: uuid.UUID) -> None:
        super().__init__(f"No discount with id '{discount_id}' exists.")
        self.discount_id = discount_id


class DiscountExpiredError(ValueError):
    """Raised when a discount's validity window has passed."""

    def __init__(self, discount_id: uuid.UUID, valid_to: datetime.datetime) -> None:
        super().__init__(
            f"Discount '{discount_id}' expired on {valid_to.isoformat()}."
        )
        self.discount_id = discount_id
        self.valid_to = valid_to


class DiscountNotYetValidError(ValueError):
    """Raised when a discount's valid_from is in the future."""

    def __init__(self, discount_id: uuid.UUID, valid_from: datetime.datetime) -> None:
        super().__init__(
            f"Discount '{discount_id}' is not yet valid (starts {valid_from.isoformat()})."
        )
        self.discount_id = discount_id
        self.valid_from = valid_from


class DiscountExceedsLineTotalError(ValueError):
    """Raised when the discount would produce a negative line total."""

    def __init__(
        self,
        discount_id: uuid.UUID,
        discount_value: decimal.Decimal,
        gross_line_amount: decimal.Decimal,
    ) -> None:
        super().__init__(
            f"Discount '{discount_id}' value {discount_value} exceeds line "
            f"gross amount {gross_line_amount}."
        )
        self.discount_id = discount_id
        self.discount_value = discount_value
        self.gross_line_amount = gross_line_amount


class DiscountProductMismatchError(ValueError):
    """Raised when a product-scoped discount references a different product."""

    def __init__(
        self,
        discount_id: uuid.UUID,
        discount_product_id: uuid.UUID,
        line_product_id: uuid.UUID,
    ) -> None:
        super().__init__(
            f"Discount '{discount_id}' is scoped to product '{discount_product_id}' "
            f"but order line uses product '{line_product_id}'."
        )
        self.discount_id = discount_id
        self.discount_product_id = discount_product_id
        self.line_product_id = line_product_id


class DiscountCategoryMismatchError(ValueError):
    """Raised when a category-scoped discount's category doesn't match the product."""

    def __init__(
        self,
        discount_id: uuid.UUID,
        discount_category_id: uuid.UUID,
        product_category_id: uuid.UUID | None,
    ) -> None:
        if product_category_id is None:
            detail = (
                f"Discount '{discount_id}' is scoped to category "
                f"'{discount_category_id}' but the product has no category."
            )
        else:
            detail = (
                f"Discount '{discount_id}' is scoped to category "
                f"'{discount_category_id}' but the product belongs to "
                f"category '{product_category_id}'."
            )
        super().__init__(detail)
        self.discount_id = discount_id
        self.discount_category_id = discount_category_id
        self.product_category_id = product_category_id


class DiscountCustomerMismatchError(ValueError):
    """Raised when a customer-scoped discount references a different customer."""

    def __init__(
        self,
        discount_id: uuid.UUID,
        discount_customer_id: uuid.UUID,
        order_customer_id: uuid.UUID,
    ) -> None:
        super().__init__(
            f"Discount '{discount_id}' is scoped to customer "
            f"'{discount_customer_id}' but order belongs to customer "
            f"'{order_customer_id}'."
        )
        self.discount_id = discount_id
        self.discount_customer_id = discount_customer_id
        self.order_customer_id = order_customer_id


class DiscountRepresentativeMismatchError(ValueError):
    """Raised when a rep-scoped discount references a different representative."""

    def __init__(
        self,
        discount_id: uuid.UUID,
        discount_rep_id: uuid.UUID,
        order_rep_id: uuid.UUID,
    ) -> None:
        super().__init__(
            f"Discount '{discount_id}' is scoped to representative "
            f"'{discount_rep_id}' but order belongs to representative "
            f"'{order_rep_id}'."
        )
        self.discount_id = discount_id
        self.discount_rep_id = discount_rep_id
        self.order_rep_id = order_rep_id


# ---------------------------------------------------------------------------
# Discount calculation
# ---------------------------------------------------------------------------


def calculate_discount(
    discount: Discount,
    *,
    unit_price: decimal.Decimal,
    qty: decimal.Decimal,
) -> decimal.Decimal:
    """Calculate the monetary discount value for a single order line.

    PERCENT: ``gross_line_amount × (value / 100)``
    AMOUNT: ``value`` (fixed currency amount)

    Returns:
        The discount value (always ≥ 0).

    Raises:
        ValueError: if ``discount_type`` is not PERCENT or AMOUNT.
    """
    gross = decimal.Decimal(unit_price) * decimal.Decimal(qty)

    if discount.discount_type == "PERCENT":
        return gross * decimal.Decimal(discount.value) / decimal.Decimal("100")
    elif discount.discount_type == "AMOUNT":
        return decimal.Decimal(discount.value)
    else:
        raise ValueError(f"Unknown discount_type: {discount.discount_type}")


# ---------------------------------------------------------------------------
# Discount validity
# ---------------------------------------------------------------------------


def validate_discount_validity(
    discount: Discount,
    *,
    at: datetime.datetime | None = None,
) -> None:
    """Verify the discount is currently valid (within its time window).

    Raises:
        DiscountNotFoundError: if the discount row is None (caller's
            responsibility to look up; this is a safety check).
        DiscountExpiredError: if valid_to is in the past.
        DiscountNotYetValidError: if valid_from is in the future.
    """
    if at is None:
        at = datetime.datetime.now(datetime.timezone.utc)

    if discount.valid_from > at:
        raise DiscountNotYetValidError(discount.id, discount.valid_from)

    if discount.valid_to is not None and discount.valid_to <= at:
        raise DiscountExpiredError(discount.id, discount.valid_to)


# ---------------------------------------------------------------------------
# Discount applicability
# ---------------------------------------------------------------------------


def validate_discount_applicability(
    session: Session,
    discount: Discount,
    *,
    product_id: uuid.UUID,
    customer_id: uuid.UUID,
    representative_id: uuid.UUID,
) -> None:
    """Verify the discount is applicable to the given order line context.

    Checks each non-NULL scope field on the discount against the
    corresponding entity on the order line / order:
    * product_id → must match the line's product
    * category_id → must match the line product's category
    * customer_id → must match the order's customer
    * representative_id → must match the order's representative

    NULL scope fields are treated as "not scoped to that dimension"
    (the discount applies regardless of that dimension).

    Per ``06_ERD.md`` H3: four nullable FKs, any combination valid.
    No precedence or stacking — Phase A applies exactly one discount.

    Raises:
        DiscountProductMismatchError
        DiscountCategoryMismatchError
        DiscountCustomerMismatchError
        DiscountRepresentativeMismatchError
    """
    # --- Product scope ---
    if discount.product_id is not None:
        if discount.product_id != product_id:
            raise DiscountProductMismatchError(
                discount.id, discount.product_id, product_id,
            )

    # --- Category scope ---
    if discount.category_id is not None:
        product = session.execute(
            select(Product).where(Product.id == product_id)
        ).scalar_one_or_none()
        if product is None:
            raise DiscountCategoryMismatchError(
                discount.id, discount.category_id, None,
            )
        if product.category_id != discount.category_id:
            raise DiscountCategoryMismatchError(
                discount.id, discount.category_id, product.category_id,
            )

    # --- Customer scope ---
    if discount.customer_id is not None:
        if discount.customer_id != customer_id:
            raise DiscountCustomerMismatchError(
                discount.id, discount.customer_id, customer_id,
            )

    # --- Representative scope ---
    if discount.representative_id is not None:
        if discount.representative_id != representative_id:
            raise DiscountRepresentativeMismatchError(
                discount.id, discount.representative_id, representative_id,
            )


# ---------------------------------------------------------------------------
# Combined: validate + calculate
# ---------------------------------------------------------------------------


def resolve_discount_for_line(
    session: Session,
    discount_id: uuid.UUID,
    *,
    product_id: uuid.UUID,
    customer_id: uuid.UUID,
    representative_id: uuid.UUID,
    unit_price: decimal.Decimal,
    qty: decimal.Decimal,
    at: datetime.datetime | None = None,
) -> tuple[Discount, decimal.Decimal]:
    """Full resolution: look up, validate validity, validate applicability,
    calculate, and return the discount + its monetary value.

    This is the single entry point for ``order_service`` to resolve a
    discount for an order line.

    Returns:
        A tuple of (Discount, discount_value).

    Raises:
        DiscountNotFoundError
        DiscountExpiredError / DiscountNotYetValidError
        DiscountProductMismatchError / DiscountCategoryMismatchError
        DiscountCustomerMismatchError / DiscountRepresentativeMismatchError
        DiscountExceedsLineTotalError
    """
    discount = session.execute(
        select(Discount).where(Discount.id == discount_id)
    ).scalar_one_or_none()
    if discount is None:
        raise DiscountNotFoundError(discount_id)

    validate_discount_validity(discount, at=at)

    validate_discount_applicability(
        session,
        discount,
        product_id=product_id,
        customer_id=customer_id,
        representative_id=representative_id,
    )

    discount_value = calculate_discount(
        discount, unit_price=unit_price, qty=qty,
    )

    gross = decimal.Decimal(unit_price) * decimal.Decimal(qty)
    if discount_value > gross:
        raise DiscountExceedsLineTotalError(discount.id, discount_value, gross)

    return discount, discount_value


__all__ = [
    "DiscountNotFoundError",
    "DiscountExpiredError",
    "DiscountNotYetValidError",
    "DiscountExceedsLineTotalError",
    "DiscountProductMismatchError",
    "DiscountCategoryMismatchError",
    "DiscountCustomerMismatchError",
    "DiscountRepresentativeMismatchError",
    "calculate_discount",
    "validate_discount_validity",
    "validate_discount_applicability",
    "resolve_discount_for_line",
]
