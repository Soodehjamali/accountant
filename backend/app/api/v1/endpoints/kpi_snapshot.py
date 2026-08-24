"""KPI Snapshot endpoints: ``/api/v1/kpi-snapshots``.

Thin HTTP wrappers around ``services.kpi_snapshot_service`` -- business
rules live there, per this project's layering rule.

* ``POST /kpi-snapshots/capture`` -- triggers ``capture_global_kpis()``
  on demand, gated behind ``KPI_SNAPSHOT_MANAGE`` permission.
* ``GET /kpi-snapshots/{kpi_key}/latest`` -- returns the most recent
  captured row for that key/scope, gated behind ``KPI_SNAPSHOT_VIEW``.
* ``GET /kpi-snapshots/{kpi_key}/history`` -- trend-chart read path,
  gated behind ``KPI_SNAPSHOT_VIEW``.

Both permission codes are auto-seeded in
``bootstrap_service._ADMIN_DEFAULT_PERMISSIONS``, so the ADMIN role
holds them by default.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.kpi_snapshot import (
    KpiCaptureRequest,
    KpiCaptureResponse,
    KpiSnapshotListResponse,
    KpiSnapshotResponse,
)
from database.models.app_user import AppUser
from services import kpi_snapshot_service

router = APIRouter(prefix="/kpi-snapshots", tags=["kpi-snapshots"])

KPI_SNAPSHOT_VIEW_PERMISSION_CODE = "KPI_SNAPSHOT_VIEW"
KPI_SNAPSHOT_MANAGE_PERMISSION_CODE = "KPI_SNAPSHOT_MANAGE"
_require_kpi_snapshot_view = require_permission(KPI_SNAPSHOT_VIEW_PERMISSION_CODE)
_require_kpi_snapshot_manage = require_permission(
    KPI_SNAPSHOT_MANAGE_PERMISSION_CODE
)


@router.post(
    "/capture",
    response_model=KpiCaptureResponse,
    summary="Trigger on-demand KPI capture (GLOBAL scope)",
)
def capture_kpis(
    body: KpiCaptureRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_kpi_snapshot_manage),
) -> KpiCaptureResponse:
    try:
        snapshots = kpi_snapshot_service.capture_global_kpis(
            db,
            period_granularity=body.period_granularity.value,
            actor_user_id=current_user.id,
        )
    except kpi_snapshot_service.InvalidPeriodGranularityError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except kpi_snapshot_service.DuplicateKpiSnapshotError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return KpiCaptureResponse(
        items=[KpiSnapshotResponse.model_validate(s) for s in snapshots],
        captured_at=snapshots[0].captured_at if snapshots else None,
    )


@router.get(
    "/{kpi_key}/latest",
    response_model=KpiSnapshotResponse | None,
    summary="Get the most recent captured KPI value",
)
def get_latest_kpi(
    kpi_key: str,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_kpi_snapshot_view),
    scope_type: str = Query("GLOBAL"),
    scope_id: uuid.UUID | None = Query(None),
) -> KpiSnapshotResponse | None:
    snapshot = kpi_snapshot_service.get_latest_kpi(
        db,
        kpi_key,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    if snapshot is None:
        return None
    return KpiSnapshotResponse.model_validate(snapshot)


@router.get(
    "/{kpi_key}/history",
    response_model=KpiSnapshotListResponse,
    summary="KPI trend-chart history (ordered by captured_at DESC)",
)
def list_kpi_history(
    kpi_key: str,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_kpi_snapshot_view),
    scope_type: str = Query("GLOBAL"),
    scope_id: uuid.UUID | None = Query(None),
    period_granularity: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> KpiSnapshotListResponse:
    items = kpi_snapshot_service.list_kpi_history(
        db,
        kpi_key,
        scope_type=scope_type,
        scope_id=scope_id,
        period_granularity=period_granularity,
        skip=skip,
        limit=limit,
    )
    return KpiSnapshotListResponse(
        items=[KpiSnapshotResponse.model_validate(i) for i in items]
    )


__all__ = ["router"]
