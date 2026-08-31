"""Service layer for the Customer aggregate (``customer`` / M8).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job. Mirrors the structure already established
by ``services/rbac_service.py`` and ``services/product_service.py``.

``Customer`` is its own Aggregate Root (see ``database/models/customer.py``
docstring, citing ``CLAUDE.md``'s "Customer is an Aggregate Root." rule).
It also "cannot hard-delete" per the ERD's own business-constraint note --
this module never issues a SQL DELETE against ``customer``; deactivation
is a status/soft-delete change only (``deactivate_customer`` below).

NOTE on scope: the ERD also documents a cross-table/temporal rule --
"credit-limit violations block new order submission" -- but that rule is
about *order submission*, not about anything this module (customer CRUD)
does on its own, so it is intentionally not implemented here. It belongs
in the future order/sales service, at the point an order is submitted.
"""

from __future__ import annotations

import decimal
import uuid
from collections.abc import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from database.models.customer import Customer


class DuplicateCustomerCodeError(ValueError):
    """Raised when ``create_customer`` is called with a ``code`` already in use."""

    def __init__(self, code: str) -> None:
        super().__init__(f"A customer with code '{code}' already exists.")
        self.code = code


class CustomerNotFoundError(LookupError):
    """Raised when a referenced ``customer_id`` has no matching, non-deleted row."""

    def __init__(self, customer_id: uuid.UUID) -> None:
        super().__init__(f"No customer with id '{customer_id}' exists.")
        self.customer_id = customer_id


def _get_customer_or_raise(session: Session, customer_id: uuid.UUID) -> Customer:
    customer = session.execute(
        select(Customer).where(Customer.id == customer_id, Customer.deleted_at.is_(None))
    ).scalar_one_or_none()
    if customer is None:
        raise CustomerNotFoundError(customer_id)
    return customer


def create_customer(
    session: Session,
    *,
    code: str,
    name: str,
    type: str,
    currency_id: uuid.UUID,
    created_by: uuid.UUID,
    city_ref_id: uuid.UUID | None = None,
    billing_address: str | None = None,
    credit_limit_amount: decimal.Decimal = decimal.Decimal("0"),
    tax_number: str | None = None,
) -> Customer:
    """Create and return a new ``Customer``, defaulting ``status`` to ``ACTIVE``.

    Raises:
        DuplicateCustomerCodeError: if ``code`` is already taken (including
            by a soft-deleted row -- ``code`` is a hard unique DB
            constraint with no partial-index carve-out for deleted rows).
    """

    existing = session.execute(
        select(Customer).where(Customer.code == code)
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateCustomerCodeError(code)

    customer = Customer(
        code=code,
        name=name,
        type=type,
        currency_id=currency_id,
        city_ref_id=city_ref_id,
        billing_address=billing_address,
        credit_limit_amount=credit_limit_amount,
        tax_number=tax_number,
        status="ACTIVE",
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(customer)
    session.flush()
    return customer


def get_customer(session: Session, customer_id: uuid.UUID) -> Customer:
    """Return a single, non-soft-deleted ``Customer``.

    Raises:
        CustomerNotFoundError: no matching row.
    """

    return _get_customer_or_raise(session, customer_id)


def list_customers(
    session: Session,
    *,
    search: str | None = None,
    status: str | None = None,
    representative_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 50,
) -> Iterable[Customer]:
    """List non-soft-deleted customers, optionally filtered.

    ``search`` matches (case-insensitively) against ``name``, ``code``, or
    ``tax_number`` -- the real model has no ``phone``/``national_id``
    columns (those belonged to the mismatched previous schema), so search
    is scoped to the fields that actually exist.

    ``representative_id``: when set, only returns customers that have an
    active ``CustomerRepAssignment`` linking to the given representative
    (time-window check: ``effective_from <= now AND (effective_to IS NULL
    OR effective_to > now)``).  This is the list-scope filtering
    counterpart of the single-customer ``_require_customer_scope`` check
    used by the GET-by-id endpoint.
    """
    import datetime

    from database.models.customer_rep_assignment import CustomerRepAssignment

    query = select(Customer).where(Customer.deleted_at.is_(None))
    if status is not None:
        query = query.where(Customer.status == status)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                Customer.name.ilike(pattern),
                Customer.code.ilike(pattern),
                Customer.tax_number.ilike(pattern),
            )
        )
    if representative_id is not None:
        # Subquery: customers with an active assignment to this representative.
        now = datetime.datetime.now(datetime.timezone.utc)
        scoped_customer_ids = (
            select(CustomerRepAssignment.customer_id)
            .where(
                CustomerRepAssignment.representative_id == representative_id,
                CustomerRepAssignment.effective_from <= now,
                (
                    CustomerRepAssignment.effective_to.is_(None)
                    | (CustomerRepAssignment.effective_to > now)
                ),
            )
            .distinct()
        )
        query = query.where(Customer.id.in_(scoped_customer_ids))
    query = query.order_by(Customer.name).offset(skip).limit(limit)
    return session.execute(query).scalars().all()


def update_customer(
    session: Session,
    customer_id: uuid.UUID,
    *,
    updated_by: uuid.UUID,
    name: str | None = None,
    city_ref_id: uuid.UUID | None = None,
    billing_address: str | None = None,
    credit_limit_amount: decimal.Decimal | None = None,
    tax_number: str | None = None,
    status: str | None = None,
) -> Customer:
    """Patch-update a ``Customer``. Only non-``None`` arguments are applied.

    Raises:
        CustomerNotFoundError: no matching row.
    """

    customer = _get_customer_or_raise(session, customer_id)
    if name is not None:
        customer.name = name
    if city_ref_id is not None:
        customer.city_ref_id = city_ref_id
    if billing_address is not None:
        customer.billing_address = billing_address
    if credit_limit_amount is not None:
        customer.credit_limit_amount = credit_limit_amount
    if tax_number is not None:
        customer.tax_number = tax_number
    if status is not None:
        customer.status = status
    customer.updated_by = updated_by
    session.flush()
    return customer


def deactivate_customer(session: Session, customer_id: uuid.UUID, *, updated_by: uuid.UUID) -> Customer:
    """Set ``status = INACTIVE``. Never a hard delete -- see module docstring.

    Raises:
        CustomerNotFoundError: no matching row.
    """

    customer = _get_customer_or_raise(session, customer_id)
    customer.status = "INACTIVE"
    customer.updated_by = updated_by
    session.flush()
    return customer


def reactivate_customer(session: Session, customer_id: uuid.UUID, *, updated_by: uuid.UUID) -> Customer:
    """Set ``status = ACTIVE``. Counterpart to ``deactivate_customer``.

    Existing orders/invoices/payments are not affected -- this is a
    lifecycle status change only, not a data mutation.

    Raises:
        CustomerNotFoundError: no matching row.
        CustomerAlreadyActiveError: customer is already ACTIVE.
    """

    customer = _get_customer_or_raise(session, customer_id)
    if customer.status == "ACTIVE":
        raise CustomerAlreadyActiveError(customer_id)
    customer.status = "ACTIVE"
    customer.updated_by = updated_by
    session.flush()
    return customer


class CustomerAlreadyActiveError(ValueError):
    """Raised when ``reactivate_customer`` is called on an already ACTIVE customer."""

    def __init__(self, customer_id: uuid.UUID) -> None:
        super().__init__(f"Customer '{customer_id}' is already ACTIVE.")
        self.customer_id = customer_id


__all__ = [
    "CustomerAlreadyActiveError",
    "CustomerNotFoundError",
    "DuplicateCustomerCodeError",
    "create_customer",
    "deactivate_customer",
    "get_customer",
    "list_customers",
    "reactivate_customer",
    "update_customer",
]
