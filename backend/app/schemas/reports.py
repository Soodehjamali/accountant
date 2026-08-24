"""Request/response schemas for the Reporting endpoints
(``/api/v1/report-definitions``, ``/api/v1/report-runs``).

Mirrors the structure established by ``app/schemas/kpi_snapshot.py``.
Aligned field-for-field with ``database/models/report_definition.py``
(M17), ``database/models/report_run.py`` (T26), and
``database/models/report_snapshot.py`` (H9).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReportDefinitionCreateRequest(BaseModel):
    """Request body for POST /report-definitions."""

    report_type_id: uuid.UUID
    name: str = Field(..., max_length=160)
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_format: str = Field(default="PDF", max_length=16)
    schedule_cron: str | None = Field(default=None, max_length=60)


class ReportDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_type_id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    parameters: dict[str, Any]
    schedule_cron: str | None
    output_format: str
    is_active: bool
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ReportRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_definition_id: uuid.UUID
    generated_document_id: uuid.UUID | None
    triggered_by: uuid.UUID | None
    status: str
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None
    row_count: int | None
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ReportSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_run_id: uuid.UUID
    report_definition_id: uuid.UUID
    snapshot_data: dict[str, Any]
    captured_at: datetime.datetime
    row_count: int
    created_by: uuid.UUID | None
    created_at: datetime.datetime


class ReportRunWithSnapshotResponse(BaseModel):
    """Response from POST /report-definitions/{id}/run -- includes the
    run status and the snapshot data inline when COMPLETE.
    """

    run: ReportRunResponse
    snapshot: ReportSnapshotResponse | None = None


class ReportRunListResponse(BaseModel):
    items: list[ReportRunResponse]


__all__ = [
    "ReportDefinitionCreateRequest",
    "ReportDefinitionResponse",
    "ReportRunListResponse",
    "ReportRunResponse",
    "ReportRunWithSnapshotResponse",
    "ReportSnapshotResponse",
]
