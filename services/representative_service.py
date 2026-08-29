"""Service layer for the Representative aggregate (M6).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's (endpoint's) job.

``Representative`` is a master data entity (M + soft-deletable).
Offboarding requires stock transferred back -- a cross-table rule
enforced at the service layer, not here.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.representative import Representative


class DuplicateRepresentativeCodeError(ValueError):
    """Raised when ``create_representative`` is called with a ``code`` already in use."""

    def __init__(self, code: str) -> None:
        super().__init__(f"A representative with code '{code}' already exists.")
        self.code = code


class RepresentativeNotFoundError(LookupError):
    """Raised when a referenced ``representative_id`` has no matching row."""

    def __init__(self, representative_id: uuid.UUID) -> None:
        super().__init__(f"No representative with id '{representative_id}' exists.")
        self.representative_id = representative_id


class RepresentativeNotDeactivatableError(ValueError):
    """Raised when attempting to deactivate a representative with active stock."""

    def __init__(self, representative_id: uuid.UUID) -> None:
        super().__init__(
            f"Representative '{representative_id}' cannot be deactivated: "
            "active stock assignments remain."
        )
        self.representative_id = representative_id


def _get_representative_or_raise(session: Session, representative_id: uuid.UUID) -> Representative:
    rep = session.execute(
        select(Representative).where(
            Representative.id == representative_id,
            Representative.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if rep is None:
        raise RepresentativeNotFoundError(representative_id)
    return rep


def create_representative(
    session: Session,
    *,
    code: str,
    person_name: str,
    created_by: uuid.UUID,
    national_id: str | None = None,
    tax_id: str | None = None,
    home_city_ref_id: uuid.UUID | None = None,
) -> Representative:
    """Create and return a new Representative, defaulting status to ACTIVE.

    Raises:
        DuplicateRepresentativeCodeError: if ``code`` is already taken.
    """
    existing = session.execute(
        select(Representative).where(Representative.code == code)
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateRepresentativeCodeError(code)

    rep = Representative(
        code=code,
        person_name=person_name,
        national_id=national_id,
        tax_id=tax_id,
        home_city_ref_id=home_city_ref_id,
        status="ACTIVE",
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(rep)
    session.flush()
    return rep


def get_representative(session: Session, representative_id: uuid.UUID) -> Representative:
    """Return a single representative. Raises: RepresentativeNotFoundError."""
    return _get_representative_or_raise(session, representative_id)


def list_representatives(
    session: Session,
    *,
    status: str | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> Iterable[Representative]:
    """List non-deleted representatives, optionally filtered."""
    query = select(Representative).where(Representative.deleted_at.is_(None))
    if status is not None:
        query = query.where(Representative.status == status)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Representative.person_name.ilike(pattern)
            | Representative.code.ilike(pattern)
        )
    query = query.order_by(Representative.person_name).offset(skip).limit(limit)
    return session.execute(query).scalars().all()


def update_representative(
    session: Session,
    representative_id: uuid.UUID,
    *,
    updated_by: uuid.UUID,
    person_name: str | None = None,
    national_id: str | None = None,
    tax_id: str | None = None,
    home_city_ref_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Representative:
    """Update an existing representative.

    Raises: RepresentativeNotFoundError.
    """
    rep = _get_representative_or_raise(session, representative_id)

    if person_name is not None:
        rep.person_name = person_name
    if national_id is not None:
        rep.national_id = national_id
    if tax_id is not None:
        rep.tax_id = tax_id
    if home_city_ref_id is not None:
        rep.home_city_ref_id = home_city_ref_id
    if status is not None:
        rep.status = status
    rep.updated_by = updated_by
    session.flush()
    return rep


def deactivate_representative(
    session: Session,
    representative_id: uuid.UUID,
    *,
    updated_by: uuid.UUID,
) -> Representative:
    """Soft-delete a representative (status -> OFFBOARDED, deleted_at set).

    Raises:
        RepresentativeNotFoundError.
        RepresentativeNotDeactivatableError: if the representative has
            active warehouse assignments with stock.
    """
    rep = _get_representative_or_raise(session, representative_id)

    # Business constraint: cannot offboard if representative has active
    # warehouse assignments.  This is a simplified check -- the full rule
    # requires checking inventory_transaction balance at assigned warehouses.
    from database.models.warehouse_assignment import WarehouseAssignment
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    has_active_assignment = session.execute(
        select(WarehouseAssignment.warehouse_id).where(
            WarehouseAssignment.representative_id == representative_id,
            WarehouseAssignment.effective_from <= now,
            (
                WarehouseAssignment.effective_to.is_(None)
                | (WarehouseAssignment.effective_to > now)
            ),
        ).limit(1)
    ).scalar_one_or_none()

    if has_active_assignment is not None:
        raise RepresentativeNotDeactivatableError(representative_id)

    rep.status = "OFFBOARDED"
    rep.deleted_at = now
    rep.updated_by = updated_by
    session.flush()
    return rep


__all__ = [
    "DuplicateRepresentativeCodeError",
    "RepresentativeNotFoundError",
    "RepresentativeNotDeactivatableError",
    "create_representative",
    "deactivate_representative",
    "get_representative",
    "list_representatives",
    "update_representative",
]
