"""FastAPI dependency factories for authorization.

Provides two layers of authorization:

1. ``require_permission(permission_code)`` -- checks that the caller holds
   a specific permission (e.g. ``ORDER_MANAGE``).  This is the existing
   permission gate.

2. ``require_order_scope`` -- enforces representative-scope on order
   access.  For representative-linked users, the order must belong to
   their representative (``order.representative_id == user.representative_id``).
   For admin/staff users with no representative link, access is unrestricted.

Usage on a protected endpoint::

    @router.post("/{order_id}/submit")
    def submit(
        order_id: uuid.UUID,
        db: Session = Depends(get_db),
        order: Order = Depends(require_order_scope),
    ) -> ...:
        ...
"""

import uuid
from typing import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from database.models.app_user import AppUser
from services import rbac_service


def require_permission(permission_code: str) -> Callable[..., AppUser]:
    """Return a FastAPI dependency that requires the caller to hold
    ``permission_code`` (via any assigned role), raising HTTP 403 otherwise.

    A factory (rather than a single dependency function) because each
    protected endpoint needs a *different* permission code baked in --
    mirrors the standard FastAPI "parameterized dependency" pattern.
    """

    def _dependency(
        current_user: AppUser = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> AppUser:
        if not rbac_service.user_has_permission(db, current_user.id, permission_code):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission '{permission_code}'.",
            )
        return current_user

    return _dependency


def _require_order_scope(
    order_id: uuid.UUID,
    current_user: AppUser,
    db: Session,
):
    """Enforce representative scope on order access.

    For representative-linked users (``current_user.representative_id is not None``),
    loads the order via ``order_service.get_order_for_representative()`` which
    enforces ``order.representative_id == user.representative_id`` in a single
    authorization-aware query.

    For admin/staff users with no representative link, loads the order via
    the unrestricted ``order_service.get_order()``.

    Returns the loaded ``Order`` object on success.

    Raises:
        HTTPException(404): order not found or out of representative scope.
        HTTPException(403): order belongs to a different representative.
    """
    from services import order_service

    if current_user.representative_id is not None:
        try:
            return order_service.get_order_for_representative(
                db, order_id, current_user.representative_id,
            )
        except order_service.OrderNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found.",
            )
        except order_service.OrderAccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found.",
            )
    else:
        try:
            return order_service.get_order(db, order_id)
        except order_service.OrderNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found.",
            )


def require_order_scope(
    order_id: uuid.UUID,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """FastAPI dependency that loads an order with representative scope.

    Use on any endpoint that operates on an existing order by ``order_id``
    path parameter.  Returns the loaded ``Order`` ORM object.

    Scope rules:
    - Representative-linked users can only access their own orders.
    - Admin/staff users (no representative link) can access any order.

    ``OrderAccessDeniedError`` is reported as 404 (not 403) to prevent
    existence leakage -- the caller cannot distinguish "order does not
    exist" from "order belongs to another representative".
    """
    return _require_order_scope(order_id, current_user, db)


# ------------------------------------------------------------------
# Invoice scope
# ------------------------------------------------------------------

def _require_invoice_scope(
    invoice_id: uuid.UUID,
    current_user: AppUser,
    db: Session,
):
    """Enforce representative scope on invoice access.

    Resolves the ownership chain ``Invoice → InvoiceOrder → Order →
    Representative``.  For representative-linked users the invoice
    must be linked to at least one order belonging to their
    representative.  For admin/staff users (no representative link)
    access is unrestricted.

    Returns the loaded ``Invoice`` ORM object on success.

    Raises:
        HTTPException(404): invoice not found, out of representative
        scope, or has no linked order.  All three cases return the
        same 404 to prevent existence leakage.
    """
    from sqlalchemy import select

    from database.models.invoice import Invoice
    from database.models.invoice_order import InvoiceOrder
    from database.models.order import Order
    from services.invoice_service import InvoiceNotFoundError

    # 1. Load the invoice.
    invoice = db.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    ).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    # 2. Admin/staff — unrestricted.
    if current_user.representative_id is None:
        return invoice

    # 3. Representative-linked — verify at least one linked order
    #    belongs to the caller's representative.
    has_own_order = db.execute(
        select(InvoiceOrder.invoice_id)
        .join(Order, InvoiceOrder.order_id == Order.id)
        .where(
            InvoiceOrder.invoice_id == invoice_id,
            Order.representative_id == current_user.representative_id,
        )
        .limit(1)
    ).scalar_one_or_none()

    if has_own_order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    return invoice


def require_invoice_scope(
    invoice_id: uuid.UUID,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """FastAPI dependency that loads an invoice with representative scope.

    Scope rules:
    - Representative-linked users can only access invoices linked to
      orders belonging to their representative.
    - Admin/staff users (no representative link) can access any invoice.

    Both non-existent and out-of-scope invoices return 404 to prevent
    existence leakage.
    """
    return _require_invoice_scope(invoice_id, current_user, db)


# ------------------------------------------------------------------
# Credit Note scope
# ------------------------------------------------------------------

def _require_credit_note_scope(
    credit_note_id: uuid.UUID,
    current_user: AppUser,
    db: Session,
):
    """Enforce representative scope on credit note access.

    Resolves the ownership chain ``CreditNote → Invoice →
    InvoiceOrder → Order → Representative``.  For representative-linked
    users the credit note's linked invoice must be in-scope (i.e. at
    least one of the invoice's linked orders belongs to the caller's
    representative).  For admin/staff users (no representative link)
    access is unrestricted.

    Returns the loaded ``CreditNote`` ORM object on success.

    Raises:
        HTTPException(404): credit note not found, out of representative
        scope, or its invoice has no linked order.  All three cases
        return the same 404 to prevent existence leakage.
    """
    from sqlalchemy import select

    from database.models.credit_note import CreditNote

    # 1. Load the credit note.
    credit_note = db.execute(
        select(CreditNote).where(CreditNote.id == credit_note_id)
    ).scalar_one_or_none()
    if credit_note is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credit note not found.",
        )

    # 2. Verify the linked invoice is in-scope (reuses invoice scope logic).
    _require_invoice_scope(credit_note.invoice_id, current_user, db)

    return credit_note


def require_credit_note_scope(
    credit_note_id: uuid.UUID,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """FastAPI dependency that loads a credit note with representative scope.

    Scope rules:
    - Representative-linked users can only access credit notes linked to
      invoices that belong to their representative (via the
      Invoice → InvoiceOrder → Order → Representative chain).
    - Admin/staff users (no representative link) can access any credit note.

    Both non-existent and out-of-scope credit notes return 404 to prevent
    existence leakage.
    """
    return _require_credit_note_scope(credit_note_id, current_user, db)


# ------------------------------------------------------------------
# Customer scope
# ------------------------------------------------------------------

def _require_customer_scope(
    customer_id: uuid.UUID,
    current_user: AppUser,
    db: Session,
):
    """Enforce representative scope on customer access.

    Resolves the ownership chain ``Customer → CustomerRepAssignment →
    Representative``.  For representative-linked users the customer
    must have an active assignment to their representative (time-window
    check: ``effective_from <= now AND (effective_to IS NULL OR
    effective_to > now)``).  For admin/staff users (no representative
    link) access is unrestricted.

    Returns the loaded ``Customer`` ORM object on success.

    Raises:
        HTTPException(404): customer not found or out of representative
        scope.  Both cases return the same 404 to prevent existence
        leakage.
    """
    import datetime

    from sqlalchemy import select

    from database.models.customer import Customer
    from database.models.customer_rep_assignment import CustomerRepAssignment

    # 1. Load the customer.
    customer = db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    # 2. Admin/staff — unrestricted.
    if current_user.representative_id is None:
        return customer

    # 3. Representative-linked — verify an active assignment exists.
    now = datetime.datetime.now(datetime.timezone.utc)
    has_assignment = db.execute(
        select(CustomerRepAssignment.customer_id)
        .where(
            CustomerRepAssignment.customer_id == customer_id,
            CustomerRepAssignment.representative_id == current_user.representative_id,
            CustomerRepAssignment.effective_from <= now,
            (
                CustomerRepAssignment.effective_to.is_(None)
                | (CustomerRepAssignment.effective_to > now)
            ),
        )
        .limit(1)
    ).scalar_one_or_none()

    if has_assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    return customer


# ------------------------------------------------------------------
# Payment scope
# ------------------------------------------------------------------

def _require_payment_scope(
    payment_id: uuid.UUID,
    current_user: AppUser,
    db: Session,
):
    """Enforce representative scope on payment access.

    Resolves the ownership chain ``Payment → Customer →
    CustomerRepAssignment → Representative``.  For representative-linked
    users the payment's customer must be currently assigned to their
    representative.  For admin/staff users (no representative link)
    access is unrestricted.

    Returns the loaded ``Payment`` ORM object on success.

    Raises:
        HTTPException(404): payment not found or out of representative
        scope.  Both cases return the same 404 to prevent existence
        leakage.
    """
    from sqlalchemy import select

    from database.models.payment import Payment

    # 1. Load the payment.
    payment = db.execute(
        select(Payment).where(Payment.id == payment_id)
    ).scalar_one_or_none()
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found.",
        )

    # 2. Verify the linked customer is in-scope (reuses customer scope logic).
    _require_customer_scope(payment.customer_id, current_user, db)

    return payment


def require_payment_scope(
    payment_id: uuid.UUID,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """FastAPI dependency that loads a payment with representative scope."""
    return _require_payment_scope(payment_id, current_user, db)


# ------------------------------------------------------------------
# Transfer scope
# ------------------------------------------------------------------

def _require_transfer_scope(
    transfer_id: uuid.UUID,
    current_user: AppUser,
    db: Session,
):
    """Enforce representative scope on stock transfer access.

    Resolves the ownership chain ``StockTransfer → Warehouse →
    WarehouseAssignment → Representative``.  For representative-linked
    users the transfer must involve at least one warehouse assigned to
    their representative (either source OR destination).  For admin/staff
    users (no representative link) access is unrestricted.

    This matches the bot layer's existing authorization semantics
    (``transfers_cmd.list_visible_transfers`` / ``get_visible_transfer``).

    Returns the loaded ``StockTransfer`` ORM object on success.

    Raises:
        HTTPException(404): transfer not found or out of warehouse scope.
        Both cases return the same 404 to prevent existence leakage.
    """
    from sqlalchemy import or_
    from sqlalchemy import select

    from database.models.stock_transfer import StockTransfer
    from database.models.warehouse_assignment import WarehouseAssignment

    # 1. Load the transfer.
    transfer = db.execute(
        select(StockTransfer).where(
            StockTransfer.id == transfer_id,
            StockTransfer.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if transfer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transfer not found.",
        )

    # 2. Admin/staff — unrestricted.
    if current_user.representative_id is None:
        return transfer

    # 3. Representative-linked — verify at least one warehouse (source
    #    OR destination) is assigned to the caller's representative.
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    has_warehouse = db.execute(
        select(WarehouseAssignment.warehouse_id)
        .where(
            WarehouseAssignment.representative_id == current_user.representative_id,
            WarehouseAssignment.effective_from <= now,
            (
                WarehouseAssignment.effective_to.is_(None)
                | (WarehouseAssignment.effective_to > now)
            ),
            or_(
                WarehouseAssignment.warehouse_id == transfer.source_warehouse_id,
                WarehouseAssignment.warehouse_id == transfer.destination_warehouse_id,
            ),
        )
        .limit(1)
    ).scalar_one_or_none()

    if has_warehouse is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transfer not found.",
        )

    return transfer


def require_transfer_scope(
    transfer_id: uuid.UUID,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """FastAPI dependency that loads a stock transfer with warehouse scope."""
    return _require_transfer_scope(transfer_id, current_user, db)


# ------------------------------------------------------------------
# Warehouse scope (reusable building block)
# ------------------------------------------------------------------

def _require_warehouse_scope(
    warehouse_id: uuid.UUID,
    current_user: AppUser,
    db: Session,
) -> None:
    """Enforce representative scope on warehouse access.

    Checks that the given ``warehouse_id`` is actively assigned to the
    caller's representative via ``WarehouseAssignment`` (time-window:
    ``effective_from <= now AND (effective_to IS NULL OR effective_to > now)``).

    For admin/staff users (no representative link) access is unrestricted.

    Raises:
        HTTPException(404): warehouse not found or not assigned to the
        caller's representative.  Both cases return the same 404 to
        prevent existence leakage.
    """
    import datetime

    from sqlalchemy import select

    from database.models.warehouse import Warehouse
    from database.models.warehouse_assignment import WarehouseAssignment

    # 1. Verify warehouse exists.
    wh_exists = db.execute(
        select(Warehouse.id).where(Warehouse.id == warehouse_id)
    ).scalar_one_or_none()
    if wh_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found.",
        )

    # 2. Admin/staff — unrestricted.
    if current_user.representative_id is None:
        return

    # 3. Representative-linked — verify an active assignment exists.
    now = datetime.datetime.now(datetime.timezone.utc)
    has_assignment = db.execute(
        select(WarehouseAssignment.warehouse_id)
        .where(
            WarehouseAssignment.representative_id == current_user.representative_id,
            WarehouseAssignment.warehouse_id == warehouse_id,
            WarehouseAssignment.effective_from <= now,
            (
                WarehouseAssignment.effective_to.is_(None)
                | (WarehouseAssignment.effective_to > now)
            ),
        )
        .limit(1)
    ).scalar_one_or_none()

    if has_assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found.",
        )


__all__ = ["require_order_scope", "require_permission", "require_invoice_scope", "require_credit_note_scope", "require_payment_scope", "require_transfer_scope", "_require_warehouse_scope"]
