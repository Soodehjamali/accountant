"""Request/response schemas for the KPI Snapshot endpoints
(``/api/v1/kpi-snapshots``).

Mirrors the structure established by ``app/schemas/customer_ledger.py``.
Aligned field-for-field with ``database/models/kpi_snapshot.py`` (H10).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ScopeType(str, Enum):
    GLOBAL = "GLOBAL"
    WAREHOUSE = "WAREHOUSE"
    REPRESENTATIVE = "REPRESENTATIVE"


class PeriodGranularity(str, Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class KpiSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kpi_key: str
    scope_type: ScopeType
    scope_id: uuid.UUID | None
    value: decimal.Decimal
    captured_at: datetime.datetime
    period_granularity: PeriodGranularity
    warehouse_id: uuid.UUID | None = None
    representative_id: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime.datetime


class KpiSnapshotListResponse(BaseModel):
    items: list[KpiSnapshotResponse]


class KpiCaptureRequest(BaseModel):
    """Request body for POST /kpi-snapshots/capture."""

    period_granularity: PeriodGranularity = PeriodGranularity.MONTHLY


class KpiCaptureResponse(BaseModel):
    """Response from the capture endpoint."""

    items: list[KpiSnapshotResponse]
    captured_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


__all__ = [
    "KpiCaptureRequest",
    "KpiCaptureResponse",
    "KpiSnapshotListResponse",
    "KpiSnapshotResponse",
    "PeriodGranularity",
    "ScopeType",
]
