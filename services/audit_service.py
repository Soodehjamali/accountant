"""Service layer for the system-wide audit trail (``audit_log`` / H6).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- the
caller's job. Mirrors ``services/rbac_service.py`` in shape.

``audit_log`` is append-only (AAC -- ``AppendOnlyAuditColumns``, per
``database/models/audit_log.py``'s own docstring): this module never
updates or deletes a row it has written, only ever inserts (``record``)
and reads (``list_entries`` / ``get_entry``).

This module is deliberately narrow -- it is the *mechanism* for writing
an audit row, not a decision about *which* application actions must be
audited. Per the roadmap note that flagged this as the next milestone
("RBAC/Audit needed by almost every other module"), the expectation is
that future service functions (order submission, invoice issuance, RBAC
grants, etc.) call ``record`` themselves at the point of the mutating
action, once each of those domains actually exists. No retrofitting into
``customer_service.py`` / ``rbac_service.py`` / ``product_service.py`` /
``inventory_service.py`` is done in this change -- deciding *what*
before/after payload each of those call sites should capture is a
design question for that domain's own owner, not something to guess
here silently.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.audit_log import AuditLog

#: Mirrors the DB CHECK constraint on audit_log.action verbatim (see
#: database/models/audit_log.py: ck_audit_log_action). Kept in sync with
#: that constraint deliberately -- if the constraint's vocabulary is ever
#: extended at migration time, this set must be extended alongside it.
#:
#: ``AUTHENTICATE`` / ``QUERY`` / ``ATTEMPT`` were added (migration
#: a9b8c7d6e5f4) for bot-flow audit: phone-verification results, bot data
#: queries (inventory/reports), and bot write attempts.
VALID_ACTIONS = frozenset({
    "CREATE",
    "UPDATE",
    "DELETE",
    "APPROVE",
    "REJECT",
    "OVERRIDE",
    "AUTHENTICATE",
    "QUERY",
    "ATTEMPT",
})


class InvalidAuditActionError(ValueError):
    """Raised when ``record`` is called with an ``action`` outside ``VALID_ACTIONS``."""

    def __init__(self, action: str) -> None:
        super().__init__(
            f"'{action}' is not a valid audit action; must be one of {sorted(VALID_ACTIONS)}."
        )
        self.action = action


class AuditLogEntryNotFoundError(LookupError):
    """Raised when a referenced ``entry_id`` has no matching row."""

    def __init__(self, entry_id: uuid.UUID) -> None:
        super().__init__(f"No audit_log entry with id '{entry_id}' exists.")
        self.entry_id = entry_id


def record(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Append one immutable audit row.

    ``actor_user_id`` is nullable (per the model -- e.g. system/background
    actions with no human caller). ``entity_type`` is a free-text
    discriminator (e.g. ``"customer"``, ``"order"``) matched against the
    polymorphic ``entity_id`` -- there is no DB-level FK enforcing that
    pairing, by design (see the model's own docstring).

    Raises:
        InvalidAuditActionError: ``action`` is not one of ``VALID_ACTIONS``.
    """

    if action not in VALID_ACTIONS:
        raise InvalidAuditActionError(action)

    entry = AuditLog(
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        before_json=before,
        after_json=after,
        ip_address=ip_address,
    )
    session.add(entry)
    session.flush()
    return entry


def get_entry(session: Session, entry_id: uuid.UUID) -> AuditLog:
    """Return a single audit_log row.

    Raises:
        AuditLogEntryNotFoundError: no matching row.
    """

    entry = session.get(AuditLog, entry_id)
    if entry is None:
        raise AuditLogEntryNotFoundError(entry_id)
    return entry


def list_entries(
    session: Session,
    *,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    occurred_from: datetime.datetime | None = None,
    occurred_to: datetime.datetime | None = None,
    skip: int = 0,
    limit: int = 50,
) -> Iterable[AuditLog]:
    """List audit_log rows, most recent first, with optional filters.

    The (entity_type, entity_id, occurred_at) composite index on the model
    makes the "show me this record's history" query (entity_type +
    entity_id supplied together) the efficient, intended path -- see the
    model's own docstring on that index.
    """

    query = select(AuditLog)
    if entity_type is not None:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        query = query.where(AuditLog.entity_id == entity_id)
    if actor_user_id is not None:
        query = query.where(AuditLog.actor_user_id == actor_user_id)
    if occurred_from is not None:
        query = query.where(AuditLog.occurred_at >= occurred_from)
    if occurred_to is not None:
        query = query.where(AuditLog.occurred_at <= occurred_to)
    query = query.order_by(AuditLog.occurred_at.desc()).offset(skip).limit(limit)
    return session.execute(query).scalars().all()


__all__ = [
    "VALID_ACTIONS",
    "AuditLogEntryNotFoundError",
    "InvalidAuditActionError",
    "get_entry",
    "list_entries",
    "record",
]
