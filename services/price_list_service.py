"""Service layer for the PriceList aggregate and PriceHistory price entries.

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it.

PriceHistory is append-only (H classification): rows are never updated
or deleted.  Opening a new price version closes the previous version's
``effective_to``.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Iterable

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DuplicatePriceListNameError(ValueError):
    """Raised when a price list with the same name already exists."""

    def __init__(self, name: str) -> None:
        super().__init__(f"A price list with name '{name}' already exists.")
        self.name = name


class PriceListNotFoundError(LookupError):
    """Raised when a referenced price_list_id has no matching row."""

    def __init__(self, price_list_id: uuid.UUID) -> None:
        super().__init__(f"No price list with id '{price_list_id}' exists.")
        self.price_list_id = price_list_id


class PriceListNotActiveError(ValueError):
    """Raised when attempting to add a price entry to an inactive price list."""

    def __init__(self, price_list_id: uuid.UUID) -> None:
        super().__init__(
            f"Price list '{price_list_id}' is inactive. "
            "Cannot add price entries to an inactive price list."
        )
        self.price_list_id = price_list_id


class ProductNotFoundError(LookupError):
    """Raised when a referenced product_id has no matching row."""

    def __init__(self, product_id: uuid.UUID) -> None:
        super().__init__(f"No product with id '{product_id}' exists.")
        self.product_id = product_id


class PriceEntryNotFoundError(LookupError):
    """Raised when a referenced price_history id has no matching row."""

    def __init__(self, entry_id: uuid.UUID) -> None:
        super().__init__(f"No price entry with id '{entry_id}' exists.")
        self.entry_id = entry_id


class OverlappingPriceError(ValueError):
    """Raised when a new price entry overlaps an existing one for the same product."""

    def __init__(
        self, product_id: uuid.UUID, price_list_id: uuid.UUID, effective_from: datetime.datetime
    ) -> None:
        super().__init__(
            f"An active price already exists for product '{product_id}' "
            f"in price list '{price_list_id}' at {effective_from}."
        )
        self.product_id = product_id
        self.price_list_id = price_list_id
        self.effective_from = effective_from


# ---------------------------------------------------------------------------
# Price List CRUD
# ---------------------------------------------------------------------------

def _get_price_list_or_raise(session: Session, price_list_id: uuid.UUID) -> PriceList:
    pl = session.execute(
        select(PriceList).where(PriceList.id == price_list_id)
    ).scalar_one_or_none()
    if pl is None:
        raise PriceListNotFoundError(price_list_id)
    return pl


def create_price_list(
    session: Session,
    *,
    name: str,
    price_type: str,
    currency_id: uuid.UUID,
    owner_scope: str,
    created_by: uuid.UUID,
) -> PriceList:
    """Create and return a new PriceList (defaults to is_active=True).

    Raises:
        DuplicatePriceListNameError: if ``name`` is already taken.
    """
    existing = session.execute(
        select(PriceList).where(PriceList.name == name)
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicatePriceListNameError(name)

    pl = PriceList(
        name=name,
        price_type=price_type,
        currency_id=currency_id,
        owner_scope=owner_scope,
        is_active=True,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(pl)
    session.flush()
    return pl


def get_price_list(session: Session, price_list_id: uuid.UUID) -> PriceList:
    """Return a single price list.  Raises: PriceListNotFoundError."""
    return _get_price_list_or_raise(session, price_list_id)


def list_price_lists(
    session: Session,
    *,
    price_type: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> Iterable[PriceList]:
    """List price lists with optional filters."""
    query = select(PriceList)
    if price_type is not None:
        query = query.where(PriceList.price_type == price_type)
    if is_active is not None:
        query = query.where(PriceList.is_active == is_active)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            PriceList.name.ilike(pattern) | PriceList.owner_scope.ilike(pattern)
        )
    query = query.order_by(PriceList.name).offset(skip).limit(limit)
    return session.execute(query).scalars().all()


def update_price_list(
    session: Session,
    price_list_id: uuid.UUID,
    *,
    updated_by: uuid.UUID,
    name: str | None = None,
    owner_scope: str | None = None,
) -> PriceList:
    """Update an existing price list.

    Raises: PriceListNotFoundError.

    Note: ``price_type`` and ``currency_id`` are NOT updatable — they are
    structural properties.  ``is_active`` is toggled via activate/deactivate.
    """
    pl = _get_price_list_or_raise(session, price_list_id)

    if name is not None:
        # Check for duplicate name.
        existing = session.execute(
            select(PriceList).where(PriceList.name == name, PriceList.id != price_list_id)
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicatePriceListNameError(name)
        pl.name = name
    if owner_scope is not None:
        pl.owner_scope = owner_scope
    pl.updated_by = updated_by
    session.flush()
    return pl


def deactivate_price_list(
    session: Session,
    price_list_id: uuid.UUID,
    *,
    updated_by: uuid.UUID,
) -> PriceList:
    """Deactivate a price list (is_active -> False).

    Raises: PriceListNotFoundError.
    """
    pl = _get_price_list_or_raise(session, price_list_id)
    if not pl.is_active:
        # Already inactive — idempotent.
        return pl
    pl.is_active = False
    pl.updated_by = updated_by
    session.flush()
    return pl


def activate_price_list(
    session: Session,
    price_list_id: uuid.UUID,
    *,
    updated_by: uuid.UUID,
) -> PriceList:
    """Activate a price list (is_active -> True).

    Raises: PriceListNotFoundError.
    """
    pl = _get_price_list_or_raise(session, price_list_id)
    if pl.is_active:
        return pl
    pl.is_active = True
    pl.updated_by = updated_by
    session.flush()
    return pl


# ---------------------------------------------------------------------------
# Price History (price entry) CRUD
# ---------------------------------------------------------------------------

_TICK = datetime.timedelta(microseconds=1)


def _get_entry_or_raise(session: Session, entry_id: uuid.UUID) -> PriceHistory:
    entry = session.execute(
        select(PriceHistory).where(PriceHistory.id == entry_id)
    ).scalar_one_or_none()
    if entry is None:
        raise PriceEntryNotFoundError(entry_id)
    return entry


def _validate_product_exists(session: Session, product_id: uuid.UUID) -> None:
    """Raise ProductNotFoundError if product does not exist."""
    exists = session.execute(
        select(func.count()).select_from(Product).where(Product.id == product_id)
    ).scalar_one()
    if exists == 0:
        raise ProductNotFoundError(product_id)


def _check_overlapping_price(
    session: Session,
    *,
    product_id: uuid.UUID,
    price_list_id: uuid.UUID,
    price_type: str,
    effective_from: datetime.datetime,
) -> None:
    """Raise OverlappingPriceError if a conflicting open window exists.

    After the previous version has been closed (effective_to set), this
    checks for any remaining open window where:
        existing.effective_from >= new.effective_from
        AND (existing.effective_to IS NULL OR existing.effective_to > effective_from)

    An entry with effective_from < new.effective_from is NOT a conflict —
    it's the version being closed by ``_close_previous_version``.
    """
    conditions = [
        PriceHistory.product_id == product_id,
        PriceHistory.price_list_id == price_list_id,
        PriceHistory.price_type == price_type,
        PriceHistory.effective_from >= effective_from,
        (PriceHistory.effective_to.is_(None) | (PriceHistory.effective_to > effective_from)),
    ]

    count = session.execute(
        select(func.count()).select_from(PriceHistory).where(and_(*conditions))
    ).scalar_one()
    if count > 0:
        raise OverlappingPriceError(product_id, price_list_id, effective_from)


def _close_previous_version(
    session: Session,
    *,
    product_id: uuid.UUID,
    price_list_id: uuid.UUID,
    price_type: str,
    new_effective_from: datetime.datetime,
) -> None:
    """Close the latest open version whose effective_from < new_effective_from.

    Sets effective_to = new_effective_from - 1 tick.
    Only closes entries whose window would still be open at
    new_effective_from (i.e. effective_to IS NULL or effective_to > new_effective_from).
    """
    prev = session.execute(
        select(PriceHistory).where(
            PriceHistory.product_id == product_id,
            PriceHistory.price_list_id == price_list_id,
            PriceHistory.price_type == price_type,
            PriceHistory.effective_from < new_effective_from,
            PriceHistory.effective_to.is_(None) | (PriceHistory.effective_to > new_effective_from),
        ).order_by(PriceHistory.effective_from.desc()).limit(1)
    ).scalar_one_or_none()

    if prev is not None:
        prev.effective_to = new_effective_from - _TICK


def add_price_entry(
    session: Session,
    *,
    product_id: uuid.UUID,
    price_list_id: uuid.UUID,
    unit_price: decimal.Decimal,
    effective_from: datetime.datetime,
    created_by: uuid.UUID,
    currency_id: uuid.UUID | None = None,
    reason: str | None = None,
    is_promo: bool = False,
    promo_valid_from: datetime.datetime | None = None,
    promo_valid_to: datetime.datetime | None = None,
) -> PriceHistory:
    """Add a new price version entry to a price list.

    Business rules enforced:
    1. Price list must exist and be active.
    2. Product must exist.
    3. No overlapping active price for same (product, price_type, price_list).
    4. Previous open version is closed (effective_to set).

    Raises:
        PriceListNotFoundError, PriceListNotActiveError,
        ProductNotFoundError, OverlappingPriceError.
    """
    pl = _get_price_list_or_raise(session, price_list_id)
    if not pl.is_active:
        raise PriceListNotActiveError(price_list_id)

    _validate_product_exists(session, product_id)

    # Use price list's currency and price_type if not overridden.
    if currency_id is None:
        currency_id = pl.currency_id
    price_type = pl.price_type

    # Close previous version FIRST — this sets effective_to on the
    # latest open entry whose effective_from < new_effective_from.
    # This is the business rule: adding a new version closes the old one.
    _close_previous_version(
        session,
        product_id=product_id,
        price_list_id=price_list_id,
        price_type=price_type,
        new_effective_from=effective_from,
    )

    # Overlap check AFTER closing: reject if any OTHER open window
    # (one we did NOT close above) overlaps with the new effective_from.
    _check_overlapping_price(
        session,
        product_id=product_id,
        price_list_id=price_list_id,
        price_type=price_type,
        effective_from=effective_from,
    )

    entry = PriceHistory(
        product_id=product_id,
        price_list_id=price_list_id,
        currency_id=currency_id,
        price_type=price_type,
        unit_price=unit_price,
        effective_from=effective_from,
        effective_to=None,
        is_promo=is_promo,
        promo_valid_from=promo_valid_from,
        promo_valid_to=promo_valid_to,
        reason=reason,
        created_by=created_by,
    )
    session.add(entry)
    session.flush()
    return entry


def list_price_entries(
    session: Session,
    price_list_id: uuid.UUID,
    *,
    product_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 50,
) -> Iterable[PriceHistory]:
    """List price entries for a price list, optionally filtered by product."""
    query = select(PriceHistory).where(PriceHistory.price_list_id == price_list_id)
    if product_id is not None:
        query = query.where(PriceHistory.product_id == product_id)
    query = query.order_by(
        PriceHistory.effective_from.desc()
    ).offset(skip).limit(limit)
    return session.execute(query).scalars().all()


def get_price_entry(session: Session, entry_id: uuid.UUID) -> PriceHistory:
    """Return a single price entry.  Raises: PriceEntryNotFoundError."""
    return _get_entry_or_raise(session, entry_id)


def get_current_price(
    session: Session,
    *,
    product_id: uuid.UUID,
    price_list_id: uuid.UUID,
) -> PriceHistory | None:
    """Return the currently valid price entry for a product in a price list.

    Returns None if no currently valid price exists.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    return session.execute(
        select(PriceHistory).where(
            PriceHistory.product_id == product_id,
            PriceHistory.price_list_id == price_list_id,
            PriceHistory.effective_from <= now,
            (PriceHistory.effective_to.is_(None) | (PriceHistory.effective_to > now)),
        ).order_by(PriceHistory.effective_from.desc()).limit(1)
    ).scalar_one_or_none()


__all__ = [
    "DuplicatePriceListNameError",
    "OverlappingPriceError",
    "PriceEntryNotFoundError",
    "PriceListNotActiveError",
    "PriceListNotFoundError",
    "ProductNotFoundError",
    "add_price_entry",
    "activate_price_list",
    "create_price_list",
    "deactivate_price_list",
    "get_current_price",
    "get_price_entry",
    "get_price_list",
    "list_price_entries",
    "list_price_lists",
    "update_price_list",
]
