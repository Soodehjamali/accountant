"""Audit log endpoints: ``GET /audit-log``, ``GET /audit-log/{id}``.

Read-only wrapper around ``services.audit_service`` -- there is no write
endpoint here on purpose (see that module's docstring). Both endpoints
are gated behind a new ``AUDIT_LOG_VIEW`` permission via
``require_permission``, matching ``endpoints/rbac.py`` /
``endpoints/customers.py``'s pattern: an audit trail is exactly the kind
of data that should not be readable by "any logged-in user" the way
products/inventory currently are, so this endpoint does not fall back to
that looser convention. ``AUDIT_LOG_VIEW`` is not auto-seeded -- same as
``CUSTOMER_MANAGE``, an RBAC admin must create and grant it via the
existing ``/api/v1/rbac`` endpoints.
"""

from __future__ import annotations

import datetime
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.audit_log import AuditLogListResponse, AuditLogResponse
from database.models.app_user import AppUser
from services import audit_service

router = APIRouter(prefix="/audit-log", tags=["audit-log"])

AUDIT_LOG_VIEW_PERMISSION_CODE = "AUDIT_LOG_VIEW"
_require_audit_log_view = require_permission(AUDIT_LOG_VIEW_PERMISSION_CODE)


@router.get("", response_model=AuditLogListResponse, summary="List audit log entries")
def list_audit_log(
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_audit_log_view),
    entity_type: str | None = Query(default=None),
    entity_id: uuid.UUID | None = Query(default=None),
    actor_user_id: uuid.UUID | None = Query(default=None),
    occurred_from: datetime.datetime | None = Query(default=None),
    occurred_to: datetime.datetime | None = Query(default=None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> AuditLogListResponse:
    items = audit_service.list_entries(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        skip=skip,
        limit=limit,
    )
    return AuditLogListResponse(items=list(items))


@router.get("/{entry_id}", response_model=AuditLogResponse, summary="Get one audit log entry")
def read_audit_log_entry(
    entry_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_audit_log_view),
) -> AuditLogResponse:
    try:
        return audit_service.get_entry(db, entry_id)
    except audit_service.AuditLogEntryNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


__all__ = ["router"]
