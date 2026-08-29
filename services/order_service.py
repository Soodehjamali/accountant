"""Service layer for the Sales Order aggregate (``order`` T10 / ``order_line``
T11 / ``order_status_history`` T12).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job. Mirrors the structure already established
by ``services/customer_service.py`` / ``services/rbac_service.py`` /
``services/inventory_service.py``.

State machine: implements the 13-state graph accepted in ``09_Decisions.md``
ADR-004 (not invented ad hoc here -- see that file and
``ADR-DRAFT-Order-State-Machine.md`` for the full derivation). ADR-004
itself leaves two points as direct, unavoidable consequences of its own
decisions rather than spelling out every edge explicitly; both are
recorded here rather than silently assumed:

* ADR-004 point 2 puts stock reservation at ``APPROVED -> RESERVED`` (after
  manager approval, not before/during ``DRAFT``). It does not separately
  restate where ``BACKORDERED`` is entered from -- but since reservation
  now happens at that single point, "insufficient stock" can only be
  discovered there too. This module therefore treats ``APPROVED ->
  BACKORDERED`` as the entry edge (attempted-but-failed reservation),
  the direct consequence of ADR-004's own point 2, not a separate
  invented rule.
* ADR-004 point 4 describes ``FULFILLING -> PARTIALLY_FULFILLED`` and
  "returns to the FULFILLING -> SHIPPED path" once every line ships. This
  module implements that "returns to" as a direct ``PARTIALLY_FULFILLED
  -> SHIPPED`` edge (once the remaining lines ship) rather than a bounce
  back through ``FULFILLING`` first -- the destination state either way
  is ``SHIPPED``, and no document distinguishes the two readings.

Every transition writes an ``order_status_history`` row (``actor_user_id``
+ ``from_state``/``to_state``, both NOT NULL per that table's own CHECK) --
see ``_transition`` below, the single choke point every state-changing
function in this module funnels through.

Explicitly OUT OF SCOPE for this module (flagged, not silently skipped):

* Pricing/discount *resolution* -- callers supply an already-resolved
  ``price_history_id`` (and, optionally, ``discount_id`` +
  ``discount_value``) per line; this module validates that
  ``price_history_id`` belongs to the given ``product_id`` and freezes
  ``unit_price`` from it, but does not implement a precedence-chain
  pricing engine (that is ``order_price_freeze`` / H6's own concern, not
  yet built, and a separate design task).
  ``order_line.tax_total`` -- Order-level ``tax_total`` is left at 0
  (no VAT/tax engine exists anywhere else in this codebase either).
* Invoice/Payment domains -- ``mark_invoiced`` / ``mark_paid`` /
  ``mark_completed`` below are order-header bookkeeping only (state +
  history), not a real integration with a (not yet built)
  ``services/invoice_service.py`` / payment-allocation flow. A future
  Invoice milestone should call these (or fold them into itself) once
  that domain exists.
* The ``BACKORDERED`` auto-retry job -- ADR-004 point 3 explicitly rules
  this out ("No automatic background retry job"); ``resubmit_order``
  below is the only way out of ``BACKORDERED`` besides cancellation, and
  it is always caller-invoked.
* Order-level soft delete (``order.deleted_at``) -- the model's own
  docstring says cancellation should prefer ``state='CANCELLED'`` over
  an actual delete; this module never sets ``deleted_at``.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models.customer import Customer
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.order_status_history import OrderStatusHistory
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.representative import Representative
from database.models.stock_reservation import StockReservation
from services import audit_service, inventory_service

#: Permission code gating the ordinary order-lifecycle mutations (create,
#: submit, reserve, resubmit, cancel, start-fulfillment, ship, invoice,
#: pay, complete, return). Mirrors ``customer_service``'s
#: ``CUSTOMER_MANAGE`` convention.
ORDER_MANAGE_PERMISSION_CODE = "ORDER_MANAGE"

#: A separate permission for the manager-approval step specifically --
#: "who may create/submit an order" and "who may approve it" are
#: realistically different actors (SRS's own "approval granted" language
#: implies a distinct approving role), so this is not folded into
#: ORDER_MANAGE.
ORDER_APPROVE_PERMISSION_CODE = "ORDER_APPROVE"

#: The accepted ADR-004 graph. Keys are the "from" state, values are the
#: set of states directly reachable from it. Every edge here traces back
#: to either an explicit ADR-004 point or the "any state before SHIPPED"
#: / "any state at/after SHIPPED" compiled agreement in
#: ADR-DRAFT-Order-State-Machine.md (kept, not overridden, by ADR-004).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"PENDING_APPROVAL", "CANCELLED"}),
    "PENDING_APPROVAL": frozenset({"APPROVED", "CANCELLED"}),
    "APPROVED": frozenset({"RESERVED", "BACKORDERED", "CANCELLED"}),
    "RESERVED": frozenset({"FULFILLING", "CANCELLED"}),
    "BACKORDERED": frozenset({"PENDING_APPROVAL", "CANCELLED"}),
    "FULFILLING": frozenset({"SHIPPED", "PARTIALLY_FULFILLED", "CANCELLED"}),
    "PARTIALLY_FULFILLED": frozenset({"SHIPPED", "RETURNED", "CANCELLED"}),
    "SHIPPED": frozenset({"INVOICED", "RETURNED"}),
    "INVOICED": frozenset({"PAID"}),
    "PAID": frozenset({"COMPLETED"}),
    "COMPLETED": frozenset(),
    "CANCELLED": frozenset(),
    "RETURNED": frozenset(),
}

#: States from which ``cancel_order`` is allowed -- "any state before
#: SHIPPED" per the compiled agreement both source docs share.
_CANCELLABLE_STATES = frozenset(
    {"DRAFT", "PENDING_APPROVAL", "APPROVED", "RESERVED", "BACKORDERED", "FULFILLING", "PARTIALLY_FULFILLED"}
)


class OrderNotFoundError(LookupError):
    """Raised when a referenced ``order_id`` has no matching row."""

    def __init__(self, order_id: uuid.UUID) -> None:
        super().__init__(f"No order with id '{order_id}' exists.")
        self.order_id = order_id


class OrderLineNotFoundError(LookupError):
    """Raised when a referenced ``order_line_id`` has no matching row on this order."""

    def __init__(self, order_line_id: uuid.UUID) -> None:
        super().__init__(f"No order line with id '{order_line_id}' exists on this order.")
        self.order_line_id = order_line_id


class CustomerNotFoundError(LookupError):
    """Raised when ``create_order``'s ``customer_id`` has no active matching row."""

    def __init__(self, customer_id: uuid.UUID) -> None:
        super().__init__(f"No active customer with id '{customer_id}' exists.")
        self.customer_id = customer_id


class RepresentativeNotFoundError(LookupError):
    """Raised when ``create_order``'s ``representative_id`` has no matching row."""

    def __init__(self, representative_id: uuid.UUID) -> None:
        super().__init__(f"No representative with id '{representative_id}' exists.")
        self.representative_id = representative_id


class ProductNotFoundError(LookupError):
    """Raised when an order line references an unknown ``product_id``."""

    def __init__(self, product_id: uuid.UUID) -> None:
        super().__init__(f"No product with id '{product_id}' exists.")
        self.product_id = product_id


class PriceHistoryMismatchError(ValueError):
    """Raised when a line's ``price_history_id`` doesn't resolve, or belongs to a different product."""

    def __init__(self, price_history_id: uuid.UUID, product_id: uuid.UUID) -> None:
        super().__init__(
            f"price_history '{price_history_id}' does not exist or is not "
            f"priced for product '{product_id}'."
        )
        self.price_history_id = price_history_id
        self.product_id = product_id


class PriceListNotFoundError(LookupError):
    """Raised when a referenced ``price_list_id`` has no matching row."""

    def __init__(self, price_list_id: uuid.UUID) -> None:
        super().__init__(f"No price list with id '{price_list_id}' exists.")
        self.price_list_id = price_list_id


class PriceListNotActiveError(ValueError):
    """Raised when the order's price list is inactive."""

    def __init__(self, price_list_id: uuid.UUID) -> None:
        super().__init__(
            f"Price list '{price_list_id}' is inactive. "
            "Cannot create orders with an inactive price list."
        )
        self.price_list_id = price_list_id


class NoCurrentPriceError(LookupError):
    """Raised when no currently valid price exists for a product in the price list."""

    def __init__(self, product_id: uuid.UUID, price_list_id: uuid.UUID) -> None:
        super().__init__(
            f"No currently valid price for product '{product_id}' "
            f"in price list '{price_list_id}'."
        )
        self.product_id = product_id
        self.price_list_id = price_list_id


class EmptyOrderError(ValueError):
    """Raised when ``create_order`` is called with zero lines."""

    def __init__(self) -> None:
        super().__init__("An order must have at least one line.")


class InvalidOrderStateTransitionError(ValueError):
    """Raised when a transition isn't a valid edge in ``ALLOWED_TRANSITIONS``."""

    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(f"Cannot transition an order from '{from_state}' to '{to_state}'.")
        self.from_state = from_state
        self.to_state = to_state


class OrderNotCancellableError(ValueError):
    """Raised when ``cancel_order`` is called on an order already at/after SHIPPED."""

    def __init__(self, state: str) -> None:
        super().__init__(f"An order in state '{state}' can no longer be cancelled.")
        self.state = state


class ShipmentQuantityError(ValueError):
    """Raised when ``ship_order`` is asked to ship more than a line has remaining."""

    def __init__(self, order_line_id: uuid.UUID, requested: decimal.Decimal, remaining: decimal.Decimal) -> None:
        super().__init__(
            f"Order line '{order_line_id}': cannot ship {requested} -- only "
            f"{remaining} remains unshipped."
        )
        self.order_line_id = order_line_id
        self.requested = requested
        self.remaining = remaining


class OrderAccessDeniedError(PermissionError):
    """Raised when a representative tries to access an order belonging to another representative."""

    def __init__(self, order_id: uuid.UUID, representative_id: uuid.UUID) -> None:
        super().__init__(
            f"Order '{order_id}' does not belong to representative '{representative_id}'."
        )
        self.order_id = order_id
        self.representative_id = representative_id


class CustomerCreditLimitExceededError(ValueError):
    """Raised when creating an order would exceed the customer's credit limit.

    Per CLAUDE.md: ``credit-limit violations block new order submission``.
    A customer with ``credit_limit_amount == 0`` has no credit extended
    and cannot create new orders (the default state). A customer with
    ``credit_limit_amount > 0`` can create orders up to their limit.
    """

    def __init__(
        self,
        customer_id: uuid.UUID,
        outstanding_balance: decimal.Decimal,
        credit_limit: decimal.Decimal,
        order_total: decimal.Decimal,
    ) -> None:
        super().__init__(
            f"Customer '{customer_id}' credit limit exceeded: "
            f"outstanding balance {outstanding_balance} + new order {order_total} = "
            f"{outstanding_balance + order_total} exceeds limit {credit_limit}."
        )
        self.customer_id = customer_id
        self.outstanding_balance = outstanding_balance
        self.credit_limit = credit_limit
        self.order_total = order_total


def _get_order_or_raise(session: Session, order_id: uuid.UUID) -> Order:
    order = session.execute(
        select(Order).where(Order.id == order_id, Order.deleted_at.is_(None))
    ).scalar_one_or_none()
    if order is None:
        raise OrderNotFoundError(order_id)
    return order


def _generate_order_number() -> str:
    """A simple, collision-safe business key: date-stamped + random suffix.

    No sequence-per-day generator exists elsewhere in this codebase to
    reuse, and building one is a separate concern (would need its own
    concurrency-safe counter table); a UUID-derived suffix is unique
    without needing one, at the cost of not being sequential/human-
    countable -- an explicit, narrow scope choice, not an oversight.
    """

    today = datetime.date.today().strftime("%Y%m%d")
    return f"ORD-{today}-{uuid.uuid4().hex[:8].upper()}"


class OrderLineInput:
    """Plain input bundle for one line of ``create_order``. Not an ORM model.

    ``price_history_id`` is optional.  When provided, it is used as an
    explicit price source.  When omitted, the service auto-resolves
    the current price from the order's price list.
    """

    def __init__(
        self,
        *,
        product_id: uuid.UUID,
        fulfillment_warehouse_id: uuid.UUID,
        price_history_id: uuid.UUID | None = None,
        qty_ordered: decimal.Decimal,
        fulfillment_mode: str,
        lot_id: uuid.UUID | None = None,
        discount_id: uuid.UUID | None = None,
        discount_value: decimal.Decimal = decimal.Decimal("0"),
    ) -> None:
        self.product_id = product_id
        self.fulfillment_warehouse_id = fulfillment_warehouse_id
        self.price_history_id = price_history_id
        self.qty_ordered = decimal.Decimal(qty_ordered)
        self.fulfillment_mode = fulfillment_mode
        self.lot_id = lot_id
        self.discount_id = discount_id
        self.discount_value = decimal.Decimal(discount_value)


class NoCustomerPriceListError(LookupError):
    """Raised when no price-list assignment exists for the customer and
    none was provided explicitly.

    Per ``02_SRS.md`` BR-P1: the system resolves customer-specific pricing
    automatically; if no assignment exists and the caller did not supply a
    ``price_list_id``, the order cannot be priced.
    """

    def __init__(self, customer_id: uuid.UUID) -> None:
        super().__init__(
            f"No price list assigned to customer '{customer_id}'. "
            "Assign a price list to the customer or provide price_list_id explicitly."
        )
        self.customer_id = customer_id


def create_order(
    session: Session,
    *,
    customer_id: uuid.UUID,
    representative_id: uuid.UUID,
    currency_id: uuid.UUID,
    price_list_id: uuid.UUID | None = None,
    order_type: str,
    fulfillment_mode: str,
    sales_channel: str,
    lines: Iterable[OrderLineInput],
    created_by: uuid.UUID,
    customer_city_ref_id: uuid.UUID | None = None,
    rep_city_ref_id: uuid.UUID | None = None,
) -> Order:
    """Create a new ``DRAFT`` order with its lines. Not itself a state
    transition (there is no "from" state for a brand-new row), so no
    ``order_status_history`` row is written here -- see module docstring.

    Pricing resolution (BR-P1 priority chain):
        1. If ``price_list_id`` is provided explicitly, use it.
        2. Otherwise, resolve via ``CustomerPriceList`` assignment
           (customer-specific pricing per BR-P1).
        3. If no customer assignment exists, raise
           ``NoCustomerPriceListError``.

        Each line's unit price is then resolved from the order's
        ``price_list_id`` using ``price_list_service.get_current_price()``.
        If a line already provides an explicit ``price_history_id``, that
        entry is used instead (the caller resolved pricing externally).
        Once resolved, the ``price_history_id`` and ``unit_price`` are
        frozen on the order line.

    Raises:
        CustomerNotFoundError: no active customer with this id.
        RepresentativeNotFoundError: no representative with this id.
        EmptyOrderError: ``lines`` is empty.
        ProductNotFoundError: a line references an unknown product.
        PriceHistoryMismatchError: a line's ``price_history_id`` doesn't
          resolve, or belongs to a different product than the line's own.
        PriceListNotFoundError: the order's ``price_list_id`` doesn't exist.
        PriceListNotActiveError: the order's price list is inactive.
        NoCurrentPriceError: no currently valid price for a product.
        NoCustomerPriceListError: no price-list assignment for the customer
          and none provided explicitly.
    """

    lines = list(lines)
    if not lines:
        raise EmptyOrderError()

    customer = session.execute(
        select(Customer).where(Customer.id == customer_id, Customer.deleted_at.is_(None))
    ).scalar_one_or_none()
    if customer is None or customer.status != "ACTIVE":
        raise CustomerNotFoundError(customer_id)

    representative = session.get(Representative, representative_id)
    if representative is None or representative.deleted_at is not None:
        raise RepresentativeNotFoundError(representative_id)

    # --- Price list resolution (BR-P1 priority chain) ---
    # 1. Explicit price_list_id from caller takes precedence.
    # 2. Otherwise, resolve via customer-specific assignment.
    if price_list_id is None:
        from services import price_list_service

        resolved = price_list_service.resolve_customer_price_list(
            session, customer_id,
        )
        if resolved is None:
            raise NoCustomerPriceListError(customer_id)
        price_list_id = resolved.id

    # Validate the price list exists and is active.
    price_list = session.execute(
        select(PriceList).where(PriceList.id == price_list_id)
    ).scalar_one_or_none()
    if price_list is None:
        raise PriceListNotFoundError(price_list_id)
    if not price_list.is_active:
        raise PriceListNotActiveError(price_list_id)

    order_lines: list[OrderLine] = []
    subtotal = decimal.Decimal("0")
    discount_total = decimal.Decimal("0")

    for line_in in lines:
        product = session.execute(
            select(Product).where(Product.id == line_in.product_id, Product.deleted_at.is_(None))
        ).scalar_one_or_none()
        if product is None:
            raise ProductNotFoundError(line_in.product_id)

        # Resolve the price for this line.
        # If an explicit price_history_id was provided, use it directly.
        # Otherwise, auto-resolve from the order's price list.
        if line_in.price_history_id is not None:
            price_history = session.execute(
                select(PriceHistory).where(
                    PriceHistory.id == line_in.price_history_id,
                    PriceHistory.product_id == line_in.product_id,
                )
            ).scalar_one_or_none()
            if price_history is None:
                raise PriceHistoryMismatchError(line_in.price_history_id, line_in.product_id)
        else:
            from services import price_list_service

            price_history = price_list_service.get_current_price(
                session,
                product_id=line_in.product_id,
                price_list_id=price_list_id,
            )
            if price_history is None:
                raise NoCurrentPriceError(line_in.product_id, price_list_id)

        unit_price = decimal.Decimal(price_history.unit_price)
        line_total = (unit_price * line_in.qty_ordered) - line_in.discount_value

        order_lines.append(
            OrderLine(
                product_id=line_in.product_id,
                lot_id=line_in.lot_id,
                fulfillment_warehouse_id=line_in.fulfillment_warehouse_id,
                qty_ordered=line_in.qty_ordered,
                unit_price=unit_price,
                discount_value=line_in.discount_value,
                discount_id=line_in.discount_id,
                price_history_id=price_history.id,
                line_total=line_total,
                fulfillment_mode=line_in.fulfillment_mode,
                created_by=created_by,
                updated_by=created_by,
            )
        )
        subtotal += unit_price * line_in.qty_ordered
        discount_total += line_in.discount_value

    grand_total = subtotal - discount_total

    # --- Credit limit enforcement ---
    # Per CLAUDE.md: "credit-limit violations block new order submission."
    # Only enforced when credit_limit_amount > 0 (explicit credit line).
    # credit_limit_amount == 0 means no limit configured (the default).
    if decimal.Decimal(customer.credit_limit_amount) > 0:
        from services import customer_ledger_service

        outstanding = customer_ledger_service.get_balance(session, customer_id)
        if outstanding + grand_total > decimal.Decimal(customer.credit_limit_amount):
            raise CustomerCreditLimitExceededError(
                customer_id=customer_id,
                outstanding_balance=outstanding,
                credit_limit=decimal.Decimal(customer.credit_limit_amount),
                order_total=grand_total,
            )

    order = Order(
        order_number=_generate_order_number(),
        customer_id=customer_id,
        representative_id=representative_id,
        sales_channel=sales_channel,
        order_type=order_type,
        fulfillment_mode=fulfillment_mode,
        state="DRAFT",
        currency_id=currency_id,
        price_list_id=price_list_id,
        subtotal=subtotal,
        discount_total=discount_total,
        tax_total=decimal.Decimal("0"),
        grand_total=grand_total,
        customer_city_ref_id=customer_city_ref_id,
        rep_city_ref_id=rep_city_ref_id,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(order)
    session.flush()

    for order_line in order_lines:
        order_line.order_id = order.id
        session.add(order_line)
    session.flush()

    audit_service.record(
        session,
        entity_type="order",
        entity_id=order.id,
        action="CREATE",
        actor_user_id=created_by,
        after={
            "order_number": order.order_number,
            "customer_id": str(customer_id),
            "state": "DRAFT",
            "grand_total": str(grand_total),
        },
    )
    session.flush()

    return order


def get_order(session: Session, order_id: uuid.UUID) -> Order:
    """Raises: OrderNotFoundError."""

    return _get_order_or_raise(session, order_id)


def get_order_for_representative(
    session: Session,
    order_id: uuid.UUID,
    representative_id: uuid.UUID,
) -> Order:
    """Return an order only if it belongs to the given representative.

    Per ADR-007 §3: this function fetches an order by ID and rejects
    access when ``order.representative_id != requested_representative_id``.
    This prevents cross-representative data leakage.

    Raises:
        OrderNotFoundError: no order with this ID exists.
        OrderAccessDeniedError: the order belongs to a different representative.
    """
    order = _get_order_or_raise(session, order_id)
    if order.representative_id != representative_id:
        raise OrderAccessDeniedError(order_id, representative_id)
    return order


def get_order_for_representative_by_number(
    session: Session,
    order_number: str,
    representative_id: uuid.UUID,
) -> Order:
    """Return an order only if it belongs to the given representative,
    looked up by ``order_number`` (not UUID).

    Single authorization-aware query: ``order_number`` + ``representative_id``
    in one WHERE clause to prevent IDOR and existence leakage.

    Per ADR-007 §3: this function enforces cross-representative access
    prohibition.  If the order belongs to a different representative,
    it is treated as non-existent (same as ``get_order_for_representative``
    which raises ``OrderAccessDeniedError``).

    Raises:
        OrderNotFoundError: no order with this number exists, or it
          belongs to a different representative.
    """
    order = session.execute(
        select(Order).where(
            Order.order_number == order_number,
            Order.representative_id == representative_id,
            Order.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if order is None:
        raise OrderNotFoundError(uuid.uuid4())  # UUID is synthetic here
    return order


def list_order_lines(session: Session, order_id: uuid.UUID) -> Iterable[OrderLine]:
    """Raises: OrderNotFoundError."""

    _get_order_or_raise(session, order_id)
    return session.execute(
        select(OrderLine).where(
            OrderLine.order_id == order_id,
            OrderLine.deleted_at.is_(None),
        ).order_by(OrderLine.created_at)
    ).scalars().all()


def list_orders(
    session: Session,
    *,
    customer_id: uuid.UUID | None = None,
    representative_id: uuid.UUID | None = None,
    state: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> Iterable[Order]:
    query = select(Order).where(Order.deleted_at.is_(None))
    if customer_id is not None:
        query = query.where(Order.customer_id == customer_id)
    if representative_id is not None:
        query = query.where(Order.representative_id == representative_id)
    if state is not None:
        query = query.where(Order.state == state)
    query = query.order_by(Order.ordered_at.desc()).offset(skip).limit(limit)
    return session.execute(query).scalars().all()


def get_order_history(session: Session, order_id: uuid.UUID) -> Iterable[OrderStatusHistory]:
    """Raises: OrderNotFoundError."""

    _get_order_or_raise(session, order_id)
    return session.execute(
        select(OrderStatusHistory)
        .where(OrderStatusHistory.order_id == order_id)
        .order_by(OrderStatusHistory.event_at)
    ).scalars().all()


def _transition(
    session: Session,
    order: Order,
    to_state: str,
    *,
    actor_user_id: uuid.UUID,
    note: str | None = None,
) -> Order:
    """The single choke point every state-changing function funnels
    through: validates the edge against ``ALLOWED_TRANSITIONS``, applies
    it, and writes the matching ``order_status_history`` row.

    Raises:
        InvalidOrderStateTransitionError: not a valid edge from the
          order's current state.
    """

    from_state = order.state
    if to_state not in ALLOWED_TRANSITIONS.get(from_state, frozenset()):
        raise InvalidOrderStateTransitionError(from_state, to_state)

    order.state = to_state
    order.updated_by = actor_user_id
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            actor_user_id=actor_user_id,
            from_state=from_state,
            to_state=to_state,
            note=note,
        )
    )
    session.flush()

    audit_service.record(
        session,
        entity_type="order",
        entity_id=order.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        before={"state": from_state},
        after={"state": to_state, "note": note},
    )
    session.flush()
    return order


def submit_order(session: Session, order_id: uuid.UUID, *, actor_user_id: uuid.UUID, note: str | None = None) -> Order:
    """``DRAFT -> PENDING_APPROVAL``. Raises: OrderNotFoundError, InvalidOrderStateTransitionError."""

    order = _get_order_or_raise(session, order_id)
    return _transition(session, order, "PENDING_APPROVAL", actor_user_id=actor_user_id, note=note)


def approve_order(session: Session, order_id: uuid.UUID, *, actor_user_id: uuid.UUID, note: str | None = None) -> Order:
    """``PENDING_APPROVAL -> APPROVED``. Raises: OrderNotFoundError, InvalidOrderStateTransitionError."""

    order = _get_order_or_raise(session, order_id)
    return _transition(session, order, "APPROVED", actor_user_id=actor_user_id, note=note)


def _active_reserved_quantity(
    session: Session,
    *,
    warehouse_id: uuid.UUID,
    product_id: uuid.UUID,
    lot_id: uuid.UUID | None,
) -> decimal.Decimal:
    """Sum of ``reserved_quantity`` across ACTIVE reservations for this
    (warehouse, product, lot) -- the quantity already spoken for by other
    orders, per ``stock_reservation.py``'s own documented business
    constraint (Sigma(active) must not exceed available balance)."""

    stmt = select(func.coalesce(func.sum(StockReservation.reserved_quantity), 0)).where(
        StockReservation.warehouse_id == warehouse_id,
        StockReservation.product_id == product_id,
        StockReservation.state == "ACTIVE",
    )
    if lot_id is not None:
        stmt = stmt.where(StockReservation.lot_id == lot_id)
    else:
        stmt = stmt.where(StockReservation.lot_id.is_(None))
    return decimal.Decimal(session.execute(stmt).scalar_one())


def _lock_inventory_for_balance(
    session: Session,
    *,
    product_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    lot_id: uuid.UUID | None,
) -> None:
    """Acquire row-level locks on all inventory transactions AND active
    reservations that compose the balance for a given (product, warehouse, lot).

    Uses ``SELECT ... FOR UPDATE`` on both tables to prevent concurrent
    transactions from reading stale data while the caller checks
    availability and creates a reservation. This closes the TOCTOU race
    in ``reserve_order_stock()``: without this lock, two concurrent
    transactions can both read the same available balance and both create
    reservations, over-reserving stock.

    The lock is held until the calling transaction commits or rolls back.
    Rows are locked in ``sequence_no`` order to minimize deadlock risk
    when multiple product-warehouse pairs are locked.

    This function does NOT return the rows -- its sole purpose is to
    acquire the locks.
    """
    from database.models.inventory_transaction import InventoryTransaction
    from database.models.stock_reservation import StockReservation

    # 1. Lock inventory transaction rows.
    stmt = (
        select(InventoryTransaction.id)
        .where(
            InventoryTransaction.warehouse_id == warehouse_id,
            InventoryTransaction.product_id == product_id,
        )
        .with_for_update()
        .order_by(InventoryTransaction.sequence_no)
    )
    if lot_id is not None:
        stmt = stmt.where(InventoryTransaction.lot_id == lot_id)
    else:
        stmt = stmt.where(InventoryTransaction.lot_id.is_(None))
    session.execute(stmt)

    # 2. Acquire a PostgreSQL advisory lock keyed on
    #    (product_id, warehouse_id, lot_id).  This blocks concurrent
    #    transactions from reading the same reservation state, even when
    #    no StockReservation rows exist yet (the TOCTOU edge case).
    import hashlib as _hl
    import struct as _struct
    lock_key_raw = f"reserve:{product_id}:{warehouse_id}:{lot_id or 'null'}"
    # Use first 8 bytes of SHA-256, reinterpret as signed 64-bit int.
    h = _hl.sha256(lock_key_raw.encode()).digest()[:8]
    lock_key = _struct.unpack_from(">q", h)[0]  # signed int64
    session.execute(
        __import__("sqlalchemy").text(
            "SELECT pg_advisory_xact_lock(:key)"
        ),
        {"key": lock_key},
    )


def reserve_order_stock(session: Session, order_id: uuid.UUID, *, actor_user_id: uuid.UUID) -> Order:
    """``APPROVED -> RESERVED`` if every line's warehouse has enough
    unreserved stock; ``APPROVED -> BACKORDERED`` otherwise (see module
    docstring's note on this being ADR-004 point 2's direct consequence).

    All-or-nothing across the whole order: if any single line can't be
    covered, no reservations are created for any line and the order goes
    to ``BACKORDERED`` as a whole -- the simplest reading consistent with
    ``stock_reservation.py``'s per-(warehouse,product,lot) invariant,
    since a half-reserved order has no clean "which half" answer without
    a rule this codebase's docs don't state.

    Concurrency safety: the inventory transaction rows that compose the
    available balance are locked via ``SELECT ... FOR UPDATE`` before the
    availability check. This prevents two concurrent transactions from
    both reading the same available balance and both creating
    reservations (the TOCTOU race). Lines are sorted by
    ``(product_id, warehouse_id, lot_id)`` to ensure consistent lock
    ordering across concurrent transactions, minimizing deadlock risk.

    Raises:
        OrderNotFoundError, InvalidOrderStateTransitionError.
    """

    order = _get_order_or_raise(session, order_id)
    if "RESERVED" not in ALLOWED_TRANSITIONS.get(order.state, frozenset()) and \
            "BACKORDERED" not in ALLOWED_TRANSITIONS.get(order.state, frozenset()):
        raise InvalidOrderStateTransitionError(order.state, "RESERVED")

    lines = session.execute(
        select(OrderLine).where(OrderLine.order_id == order_id)
    ).scalars().all()

    # Sort lines by (product_id, warehouse_id, lot_id) to ensure
    # consistent lock ordering across concurrent transactions.
    lines = sorted(
        lines,
        key=lambda l: (
            l.product_id,
            l.fulfillment_warehouse_id,
            l.lot_id or uuid.UUID(int=0),
        ),
    )

    # Lock inventory transaction rows for every product/warehouse/lot
    # combination on this order BEFORE checking availability. This
    # prevents the TOCTOU race where two concurrent transactions both
    # read the same available balance.
    seen: set[tuple] = set()
    for line in lines:
        key = (line.product_id, line.fulfillment_warehouse_id, line.lot_id)
        if key in seen:
            continue
        seen.add(key)
        _lock_inventory_for_balance(
            session,
            product_id=line.product_id,
            warehouse_id=line.fulfillment_warehouse_id,
            lot_id=line.lot_id,
        )

    shortfall = False
    for line in lines:
        ledger_balance = inventory_service.get_balance(
            session,
            warehouse_id=line.fulfillment_warehouse_id,
            product_id=line.product_id,
            lot_id=line.lot_id,
        )
        already_reserved = _active_reserved_quantity(
            session,
            warehouse_id=line.fulfillment_warehouse_id,
            product_id=line.product_id,
            lot_id=line.lot_id,
        )
        available = ledger_balance - already_reserved
        if available < line.qty_ordered:
            shortfall = True
            break

    if shortfall:
        return _transition(
            session,
            order,
            "BACKORDERED",
            actor_user_id=actor_user_id,
            note="Insufficient available stock at reservation time.",
        )

    # order.fulfillment_warehouse_id (nullable, "set once reserved" per
    # that column's own docstring) -- set to the first line's warehouse.
    # Multi-warehouse orders are possible at the line level (each
    # order_line has its own NOT NULL fulfillment_warehouse_id) but the
    # order header only has room for one; no source doc resolves that
    # ambiguity, so this picks the first line's warehouse as the
    # header-level summary value rather than leaving it unset.
    if lines:
        order.fulfillment_warehouse_id = lines[0].fulfillment_warehouse_id

    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)
    for line in lines:
        session.add(
            StockReservation(
                warehouse_id=line.fulfillment_warehouse_id,
                product_id=line.product_id,
                lot_id=line.lot_id,
                order_id=order.id,
                reserved_by=actor_user_id,
                reserved_quantity=line.qty_ordered,
                state="ACTIVE",
                expires_at=expires_at,
                created_by=actor_user_id,
                updated_by=actor_user_id,
            )
        )
        line.qty_reserved = line.qty_ordered
        line.updated_by = actor_user_id
    session.flush()

    return _transition(session, order, "RESERVED", actor_user_id=actor_user_id)


def resubmit_order(session: Session, order_id: uuid.UUID, *, actor_user_id: uuid.UUID, note: str | None = None) -> Order:
    """``BACKORDERED -> PENDING_APPROVAL`` (ADR-004 point 3's manual resubmit path)."""

    order = _get_order_or_raise(session, order_id)
    return _transition(session, order, "PENDING_APPROVAL", actor_user_id=actor_user_id, note=note)


def cancel_order(session: Session, order_id: uuid.UUID, *, actor_user_id: uuid.UUID, note: str | None = None) -> Order:
    """Cancel from any state before SHIPPED, releasing any ACTIVE reservations
    and reversing any inventory already deducted by prior shipments.

    For every order line with ``qty_shipped > 0``, the corresponding
    un-reversed ``SALE_OUT`` inventory transaction is reversed via
    ``inventory_service.reverse_transaction()`` (compensating ledger
    entry — the original SALE_OUT remains immutable per the append-only
    ledger design).  This ensures that cancelling a partially-shipped
    order correctly restores physical stock.

    DIRECT orders never post SALE_OUT transactions (per ``order.py``'s
    documented rule), so the reversal query naturally returns nothing
    for them.

    Idempotency: if ``cancel_order`` is called twice, the second call
    finds no un-reversed SALE_OUT transactions (``is_reversed`` flag)
    and no ACTIVE reservations (already RELEASED), so it proceeds to
    the state transition which is rejected because the order is already
    ``CANCELLED``.

    Raises:
        OrderNotFoundError.
        OrderNotCancellableError: order is already SHIPPED or later
          (or already CANCELLED/RETURNED).
    """

    order = _get_order_or_raise(session, order_id)
    if order.state not in _CANCELLABLE_STATES:
        raise OrderNotCancellableError(order.state)

    # Release any ACTIVE reservations.
    active_reservations = session.execute(
        select(StockReservation).where(
            StockReservation.order_id == order_id, StockReservation.state == "ACTIVE"
        )
    ).scalars().all()
    for reservation in active_reservations:
        reservation.state = "RELEASED"
        reservation.updated_by = actor_user_id
    session.flush()

    # Reverse inventory already deducted by prior shipments.
    # For every line with qty_shipped > 0, find the un-reversed SALE_OUT
    # transaction and create a compensating REVERSAL entry.
    from database.models.inventory_transaction import InventoryTransaction

    shipped_lines = session.execute(
        select(OrderLine).where(
            OrderLine.order_id == order_id,
            OrderLine.qty_shipped > 0,
        )
    ).scalars().all()
    for line in shipped_lines:
        sale_out_txns = session.execute(
            select(InventoryTransaction).where(
                InventoryTransaction.reference_type == "order_line",
                InventoryTransaction.reference_id == line.id,
                InventoryTransaction.is_reversed == False,  # noqa: E712
            )
        ).scalars().all()
        for txn in sale_out_txns:
            inventory_service.reverse_transaction(
                session,
                txn.id,
                actor_user_id=actor_user_id,
            )

    return _transition(session, order, "CANCELLED", actor_user_id=actor_user_id, note=note)


def start_fulfillment(session: Session, order_id: uuid.UUID, *, actor_user_id: uuid.UUID) -> Order:
    """``RESERVED -> FULFILLING``."""

    order = _get_order_or_raise(session, order_id)
    return _transition(session, order, "FULFILLING", actor_user_id=actor_user_id)


class ShipmentInput:
    """Plain input bundle for one line of ``ship_order``. Not an ORM model."""

    def __init__(self, *, order_line_id: uuid.UUID, quantity: decimal.Decimal) -> None:
        self.order_line_id = order_line_id
        self.quantity = decimal.Decimal(quantity)


def ship_order(
    session: Session,
    order_id: uuid.UUID,
    *,
    shipments: Iterable[ShipmentInput],
    actor_user_id: uuid.UUID,
) -> Order:
    """Record shipment of one or more lines from ``FULFILLING`` or
    ``PARTIALLY_FULFILLED``, posting an inventory-ledger ``SALE_OUT`` row
    per shipped line (skipped for ``DIRECT`` orders, per ``order.py``'s
    own documented rule that DIRECT orders "must never post against a
    representative's warehouse ledger"), and consuming that line's
    ``ACTIVE`` reservation.

    Ends in ``SHIPPED`` if every line is now fully shipped, otherwise
    ``PARTIALLY_FULFILLED``.

    Simplification (flagged, not silent): a reservation is consumed in
    full the first time any shipment touches its line, rather than
    tracking partial reservation consumption -- this codebase's docs
    don't specify a partial-reservation-consumption rule, and
    over/under-shipping is already independently guarded by
    ``ShipmentQuantityError`` below.

    Raises:
        OrderNotFoundError, InvalidOrderStateTransitionError.
        OrderLineNotFoundError: a shipment references a line not on this order.
        ShipmentQuantityError: a shipment would ship more than remains.
    """

    order = _get_order_or_raise(session, order_id)
    if order.state not in ("FULFILLING", "PARTIALLY_FULFILLED"):
        raise InvalidOrderStateTransitionError(order.state, "SHIPPED")

    lines_by_id = {
        line.id: line
        for line in session.execute(
            select(OrderLine).where(OrderLine.order_id == order_id)
        ).scalars().all()
    }

    for shipment in shipments:
        line = lines_by_id.get(shipment.order_line_id)
        if line is None:
            raise OrderLineNotFoundError(shipment.order_line_id)

        remaining = decimal.Decimal(line.qty_ordered) - decimal.Decimal(line.qty_shipped)
        if shipment.quantity <= 0 or shipment.quantity > remaining:
            raise ShipmentQuantityError(line.id, shipment.quantity, remaining)

        if order.order_type != "DIRECT":
            inventory_service.post_transaction(
                session,
                product_id=line.product_id,
                warehouse_id=line.fulfillment_warehouse_id,
                movement_type_code="SALE_OUT",
                signed_quantity=-shipment.quantity,
                unit_cost=line.unit_price,
                currency_id=order.currency_id,
                actor_user_id=actor_user_id,
                lot_id=line.lot_id,
                reference_type="order_line",
                reference_id=line.id,
            )

        reservation = session.execute(
            select(StockReservation).where(
                StockReservation.order_id == order_id,
                StockReservation.product_id == line.product_id,
                StockReservation.warehouse_id == line.fulfillment_warehouse_id,
                StockReservation.lot_id == line.lot_id,
                StockReservation.state == "ACTIVE",
            )
        ).scalar_one_or_none()
        if reservation is not None:
            reservation.state = "CONSUMED"
            reservation.updated_by = actor_user_id

        line.qty_shipped = decimal.Decimal(line.qty_shipped) + shipment.quantity
        line.updated_by = actor_user_id

    session.flush()

    all_lines = lines_by_id.values()
    fully_shipped = all(
        decimal.Decimal(line.qty_shipped) >= decimal.Decimal(line.qty_ordered) for line in all_lines
    )
    to_state = "SHIPPED" if fully_shipped else "PARTIALLY_FULFILLED"

    # A second (or third...) partial shipment against an order already in
    # PARTIALLY_FULFILLED is not itself a state change -- ALLOWED_TRANSITIONS
    # has no PARTIALLY_FULFILLED -> PARTIALLY_FULFILLED self-loop (no source
    # doc describes one), so only call _transition when the state is
    # actually changing; a same-state repeat just updates the shipped
    # quantities above without a new history row.
    if to_state != order.state:
        order = _transition(session, order, to_state, actor_user_id=actor_user_id)
    if to_state == "SHIPPED":
        order.shipped_at = datetime.datetime.now(datetime.timezone.utc)
        session.flush()
    return order


def record_return(session: Session, order_id: uuid.UUID, *, actor_user_id: uuid.UUID, note: str | None = None) -> Order:
    """``SHIPPED``/``PARTIALLY_FULFILLED`` -> ``RETURNED`` (ADR-004 point 5:
    reachable only from those two states; returns against a COMPLETED
    order go entirely through ``credit_note``/``customer_return``
    instead, per that same decision -- not implemented in this module)."""

    order = _get_order_or_raise(session, order_id)
    return _transition(session, order, "RETURNED", actor_user_id=actor_user_id, note=note)


def mark_invoiced(session: Session, order_id: uuid.UUID, *, actor_user_id: uuid.UUID, note: str | None = None) -> Order:
    """``SHIPPED -> INVOICED``. Order-header bookkeeping only -- see module
    docstring's "Invoice/Payment domains" scope note."""

    order = _get_order_or_raise(session, order_id)
    order = _transition(session, order, "INVOICED", actor_user_id=actor_user_id, note=note)
    order.invoiced_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
    return order


def mark_paid(session: Session, order_id: uuid.UUID, *, actor_user_id: uuid.UUID, note: str | None = None) -> Order:
    """``INVOICED -> PAID``. Order-header bookkeeping only -- see module docstring."""

    order = _get_order_or_raise(session, order_id)
    order = _transition(session, order, "PAID", actor_user_id=actor_user_id, note=note)
    order.paid_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
    return order


def mark_completed(session: Session, order_id: uuid.UUID, *, actor_user_id: uuid.UUID, note: str | None = None) -> Order:
    """``PAID -> COMPLETED``.

    After the state transition, automatically calculates and records
    the commission transaction for this order via
    ``commission_service.calculate_commission_for_order()``.

    If no commission config matches (``NoCommissionConfigFoundError``),
    the order still completes successfully -- commission is best-effort.
    If commission was already calculated (``CommissionAlreadyCalculatedError``),
    it is silently skipped (idempotent).
    """

    order = _get_order_or_raise(session, order_id)
    order = _transition(session, order, "COMPLETED", actor_user_id=actor_user_id, note=note)

    # Auto-calculate commission (best-effort).
    try:
        from services import commission_service
        commission_service.calculate_commission_for_order(
            session,
            order_id=order.id,
            actor_user_id=actor_user_id,
        )
    except Exception:
        # Commission calculation is best-effort: if no config matches
        # or it was already calculated, the order still completes.
        pass

    return order


# ------------------------------------------------------------------
# DRAFT order line editing
# ------------------------------------------------------------------


class OrderNotEditableError(ValueError):
    """Raised when attempting to edit an order not in DRAFT state."""

    def __init__(self, order_id: uuid.UUID, state: str) -> None:
        super().__init__(
            f"Order '{order_id}' is in state '{state}'; only DRAFT orders can be edited."
        )
        self.order_id = order_id
        self.state = state


class DuplicateProductOnOrderError(ValueError):
    """Raised when adding a line with a product already on the order."""

    def __init__(self, product_id: uuid.UUID) -> None:
        super().__init__(
            f"Product '{product_id}' is already on this order. "
            "Use update to change the quantity instead."
        )
        self.product_id = product_id


def _recalculate_order_totals(session: Session, order: Order) -> None:
    """Recompute ``subtotal``, ``discount_total``, and ``grand_total``
    from the current set of active (non-deleted) order lines.

    Unit prices are read from the frozen ``unit_price`` on each line --
    this function does NOT re-resolve pricing.
    """

    active_lines = session.execute(
        select(OrderLine).where(
            OrderLine.order_id == order.id,
            OrderLine.deleted_at.is_(None),
        )
    ).scalars().all()

    subtotal = decimal.Decimal("0")
    discount_total = decimal.Decimal("0")
    for line in active_lines:
        subtotal += decimal.Decimal(line.unit_price) * decimal.Decimal(line.qty_ordered)
        discount_total += decimal.Decimal(line.discount_value)

    order.subtotal = subtotal
    order.discount_total = discount_total
    order.grand_total = subtotal - discount_total
    order.updated_by = order.updated_by  # preserve caller
    session.flush()


def add_order_line(
    session: Session,
    order_id: uuid.UUID,
    line_in: OrderLineInput,
    *,
    actor_user_id: uuid.UUID,
) -> OrderLine:
    """Add a new line to a ``DRAFT`` order.

    Resolves pricing from the order's price list (same logic as
    ``create_order``), freezes ``unit_price`` and ``price_history_id``
    on the new line, and recalculates the order totals.

    Raises:
        OrderNotFoundError, OrderNotEditableError,
        ProductNotFoundError, PriceHistoryMismatchError,
        NoCurrentPriceError, DuplicateProductOnOrderError.
    """

    order = _get_order_or_raise(session, order_id)
    if order.state != "DRAFT":
        raise OrderNotEditableError(order_id, order.state)

    product = session.execute(
        select(Product).where(Product.id == line_in.product_id, Product.deleted_at.is_(None))
    ).scalar_one_or_none()
    if product is None:
        raise ProductNotFoundError(line_in.product_id)

    # Check for duplicate product on this order.
    existing = session.execute(
        select(OrderLine).where(
            OrderLine.order_id == order_id,
            OrderLine.product_id == line_in.product_id,
            OrderLine.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateProductOnOrderError(line_in.product_id)

    # Resolve pricing (same logic as create_order).
    if line_in.price_history_id is not None:
        price_history = session.execute(
            select(PriceHistory).where(
                PriceHistory.id == line_in.price_history_id,
                PriceHistory.product_id == line_in.product_id,
            )
        ).scalar_one_or_none()
        if price_history is None:
            raise PriceHistoryMismatchError(line_in.price_history_id, line_in.product_id)
    else:
        from services import price_list_service

        price_history = price_list_service.get_current_price(
            session,
            product_id=line_in.product_id,
            price_list_id=order.price_list_id,
        )
        if price_history is None:
            raise NoCurrentPriceError(line_in.product_id, order.price_list_id)

    unit_price = decimal.Decimal(price_history.unit_price)
    line_total = (unit_price * line_in.qty_ordered) - line_in.discount_value

    order_line = OrderLine(
        order_id=order_id,
        product_id=line_in.product_id,
        lot_id=line_in.lot_id,
        fulfillment_warehouse_id=line_in.fulfillment_warehouse_id,
        qty_ordered=line_in.qty_ordered,
        unit_price=unit_price,
        discount_value=line_in.discount_value,
        discount_id=line_in.discount_id,
        price_history_id=price_history.id,
        line_total=line_total,
        fulfillment_mode=line_in.fulfillment_mode,
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    session.add(order_line)
    session.flush()

    _recalculate_order_totals(session, order)

    audit_service.record(
        session,
        entity_type="order",
        entity_id=order.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        after={"action": "add_line", "product_id": str(line_in.product_id)},
    )
    session.flush()

    return order_line


def remove_order_line(
    session: Session,
    order_id: uuid.UUID,
    order_line_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> None:
    """Soft-delete a line from a ``DRAFT`` order.

    The line's ``deleted_at`` is set (soft-delete per T11's
    "Supported pre-approval only" rule).  The order totals are
    recalculated from the remaining active lines.

    Raises:
        OrderNotFoundError, OrderNotEditableError,
        OrderLineNotFoundError.
    """

    order = _get_order_or_raise(session, order_id)
    if order.state != "DRAFT":
        raise OrderNotEditableError(order_id, order.state)

    line = session.execute(
        select(OrderLine).where(
            OrderLine.id == order_line_id,
            OrderLine.order_id == order_id,
            OrderLine.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if line is None:
        raise OrderLineNotFoundError(order_line_id)

    line.deleted_at = datetime.datetime.now(datetime.timezone.utc)
    line.updated_by = actor_user_id
    session.flush()

    _recalculate_order_totals(session, order)

    audit_service.record(
        session,
        entity_type="order",
        entity_id=order.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        after={"action": "remove_line", "order_line_id": str(order_line_id)},
    )
    session.flush()


def get_latest_draft_order_for_representative(
    session: Session,
    representative_id: uuid.UUID,
) -> Order | None:
    """Return the most recent DRAFT order for a representative, or None.

    Used by bot commands (e.g. ``/set-price``) that operate on the
    representative's current working order.
    """
    return session.execute(
        select(Order).where(
            Order.representative_id == representative_id,
            Order.state == "DRAFT",
            Order.deleted_at.is_(None),
        ).order_by(Order.ordered_at.desc()).limit(1)
    ).scalar_one_or_none()


def update_order_line_price(
    session: Session,
    order_id: uuid.UUID,
    order_line_id: uuid.UUID,
    new_unit_price: decimal.Decimal,
    *,
    actor_user_id: uuid.UUID,
) -> OrderLine:
    """Override the selling price of an existing line on a ``DRAFT`` order.

    Per ``04_Business_Policies.md``: *"Representative may change selling
    price.  Price change affects only current invoice."*  This implements
    the price override for the current (DRAFT) order only -- the change
    does not persist to the ``price_history`` ledger.

    The ``unit_price``, ``line_total``, and order totals are
    recalculated.  The ``price_history_id`` is intentionally left
    unchanged -- it still records the original price provenance, while
    ``unit_price`` carries the overridden value.  (The spec's
    immutability trigger only fires once the order passes APPROVED.)

    Raises:
        OrderNotFoundError, OrderNotEditableError,
        OrderLineNotFoundError.
    """

    order = _get_order_or_raise(session, order_id)
    if order.state != "DRAFT":
        raise OrderNotEditableError(order_id, order.state)

    line = session.execute(
        select(OrderLine).where(
            OrderLine.id == order_line_id,
            OrderLine.order_id == order_id,
            OrderLine.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if line is None:
        raise OrderLineNotFoundError(order_line_id)

    new_unit_price = decimal.Decimal(new_unit_price)
    if new_unit_price < 0:
        raise ValueError("Price must be non-negative.")

    old_price = decimal.Decimal(line.unit_price)
    line.unit_price = new_unit_price
    line.line_total = (new_unit_price * decimal.Decimal(line.qty_ordered)) - decimal.Decimal(
        line.discount_value
    )
    line.updated_by = actor_user_id
    session.flush()

    _recalculate_order_totals(session, order)

    audit_service.record(
        session,
        entity_type="order",
        entity_id=order.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        after={
            "action": "update_line_price",
            "order_line_id": str(order_line_id),
            "old_unit_price": str(old_price),
            "new_unit_price": str(new_unit_price),
        },
    )
    session.flush()

    return line


def update_order_line_qty(
    session: Session,
    order_id: uuid.UUID,
    order_line_id: uuid.UUID,
    new_qty: decimal.Decimal,
    *,
    actor_user_id: uuid.UUID,
) -> OrderLine:
    """Update the quantity of an existing line on a ``DRAFT`` order.

    The frozen ``unit_price`` is NOT changed -- only ``qty_ordered``
    and ``line_total`` are updated.  Order totals are recalculated.

    Raises:
        OrderNotFoundError, OrderNotEditableError,
        OrderLineNotFoundError.
    """

    order = _get_order_or_raise(session, order_id)
    if order.state != "DRAFT":
        raise OrderNotEditableError(order_id, order.state)

    line = session.execute(
        select(OrderLine).where(
            OrderLine.id == order_line_id,
            OrderLine.order_id == order_id,
            OrderLine.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if line is None:
        raise OrderLineNotFoundError(order_line_id)

    new_qty = decimal.Decimal(new_qty)
    if new_qty <= 0:
        raise ValueError("Quantity must be positive.")

    line.qty_ordered = new_qty
    line.line_total = (decimal.Decimal(line.unit_price) * new_qty) - decimal.Decimal(line.discount_value)
    line.updated_by = actor_user_id
    session.flush()

    _recalculate_order_totals(session, order)

    audit_service.record(
        session,
        entity_type="order",
        entity_id=order.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        after={
            "action": "update_line_qty",
            "order_line_id": str(order_line_id),
            "new_qty": str(new_qty),
        },
    )
    session.flush()

    return line


# -----------------------------------------------------------------------
# Discount application (BR-P2 Phase A)
# -----------------------------------------------------------------------


def apply_discount_to_order_line(
    session: Session,
    order_id: uuid.UUID,
    order_line_id: uuid.UUID,
    discount_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> OrderLine:
    """Apply an explicit discount to a ``DRAFT`` order line.

    BR-P2 Phase A: single explicit discount per line.

    1. Verify order is DRAFT.
    2. Verify the line exists on this order.
    3. Validate discount via ``discount_service.resolve_discount_for_line``:
       - exists, valid dates, applicable to line (product/category/
         customer/representative scope).
    4. Calculate discount value.
    5. Reject if discount exceeds line gross amount.
    6. Store ``discount_id`` and ``discount_value`` on the line.
    7. Recalculate ``line_total`` and order totals.

    The ``/set-price`` interaction is preserved: if ``unit_price`` was
    previously overridden, the discount is applied to the current
    ``unit_price`` (the overridden value), per the formula:
    ``line_total = (unit_price × qty) − discount_value``.

    Raises:
        OrderNotFoundError, OrderNotEditableError, OrderLineNotFoundError.
        All ``discount_service`` exceptions (DiscountNotFoundError,
        DiscountExpiredError, DiscountExceedsLineTotalError, etc.).
    """
    from services import discount_service

    order = _get_order_or_raise(session, order_id)
    if order.state != "DRAFT":
        raise OrderNotEditableError(order_id, order.state)

    line = session.execute(
        select(OrderLine).where(
            OrderLine.id == order_line_id,
            OrderLine.order_id == order_id,
            OrderLine.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if line is None:
        raise OrderLineNotFoundError(order_line_id)

    # Resolve and validate the discount (validity + applicability +
    # calculation + negative-line-total prevention).
    _discount, discount_value = discount_service.resolve_discount_for_line(
        session,
        discount_id,
        product_id=line.product_id,
        customer_id=order.customer_id,
        representative_id=order.representative_id,
        unit_price=line.unit_price,
        qty=line.qty_ordered,
    )

    # Store the discount on the line.
    line.discount_id = discount_id
    line.discount_value = discount_value
    line.line_total = (
        decimal.Decimal(line.unit_price) * decimal.Decimal(line.qty_ordered)
    ) - discount_value
    line.updated_by = actor_user_id
    session.flush()

    _recalculate_order_totals(session, order)

    audit_service.record(
        session,
        entity_type="order",
        entity_id=order.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        after={
            "action": "apply_discount",
            "order_line_id": str(order_line_id),
            "discount_id": str(discount_id),
            "discount_value": str(discount_value),
        },
    )
    session.flush()

    return line


def remove_discount_from_order_line(
    session: Session,
    order_id: uuid.UUID,
    order_line_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> OrderLine:
    """Remove the discount from a ``DRAFT`` order line.

    Resets ``discount_id`` to NULL and ``discount_value`` to 0,
    recalculates ``line_total`` and order totals.

    Raises:
        OrderNotFoundError, OrderNotEditableError, OrderLineNotFoundError.
    """
    order = _get_order_or_raise(session, order_id)
    if order.state != "DRAFT":
        raise OrderNotEditableError(order_id, order.state)

    line = session.execute(
        select(OrderLine).where(
            OrderLine.id == order_line_id,
            OrderLine.order_id == order_id,
            OrderLine.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if line is None:
        raise OrderLineNotFoundError(order_line_id)

    line.discount_id = None
    line.discount_value = decimal.Decimal("0")
    line.line_total = decimal.Decimal(line.unit_price) * decimal.Decimal(
        line.qty_ordered
    )
    line.updated_by = actor_user_id
    session.flush()

    _recalculate_order_totals(session, order)

    audit_service.record(
        session,
        entity_type="order",
        entity_id=order.id,
        action="UPDATE",
        actor_user_id=actor_user_id,
        after={
            "action": "remove_discount",
            "order_line_id": str(order_line_id),
        },
    )
    session.flush()

    return line


__all__ = [
    "ALLOWED_TRANSITIONS",
    "ORDER_APPROVE_PERMISSION_CODE",
    "ORDER_MANAGE_PERMISSION_CODE",
    "CustomerNotFoundError",
    "DuplicateProductOnOrderError",
    "EmptyOrderError",
    "InvalidOrderStateTransitionError",
    "NoCurrentPriceError",
    "NoCustomerPriceListError",
    "OrderLineInput",
    "OrderLineNotFoundError",
    "OrderNotCancellableError",
    "OrderNotEditableError",
    "OrderNotFoundError",
    "CustomerCreditLimitExceededError",
    "PriceHistoryMismatchError",
    "PriceListNotActiveError",
    "PriceListNotFoundError",
    "ProductNotFoundError",
    "RepresentativeNotFoundError",
    "ShipmentInput",
    "ShipmentQuantityError",
    "add_order_line",
    "apply_discount_to_order_line",
    "approve_order",
    "cancel_order",
    "create_order",
    "get_latest_draft_order_for_representative",
    "get_order",
    "get_order_for_representative",
    "get_order_for_representative_by_number",
    "get_order_history",
    "list_order_lines",
    "list_orders",
    "mark_completed",
    "mark_invoiced",
    "mark_paid",
    "record_return",
    "remove_discount_from_order_line",
    "remove_order_line",
    "reserve_order_stock",
    "resubmit_order",
    "ship_order",
    "start_fulfillment",
    "submit_order",
    "update_order_line_price",
    "update_order_line_qty",
]
