"""Response schemas for the audit log endpoints (``/api/v1/audit-log``).

Read-only -- there is no create/update request schema, since
``audit_log`` is append-only and never written to directly via the HTTP
API (see ``services/audit_service.py``'s own docstring: future domain
services call ``record()`` themselves at the point of the mutating
action).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    entity_type: str
    entity_id: uuid.UUID
    action: str
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    occurred_at: datetime.datetime
    ip_address: str | None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]


__all__ = ["AuditLogListResponse", "AuditLogResponse"]
