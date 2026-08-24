"""Reporting endpoints: ``/api/v1/report-definitions``,
``/api/v1/report-runs``.

Thin HTTP wrappers around ``services.report_service`` -- business rules
live there, per this project's layering rule.

All endpoints gated behind ``REPORT_MANAGE`` permission via
``require_permission``, matching the convention every other domain
endpoint in this codebase documents.  ``REPORT_MANAGE`` is auto-seeded
in ``bootstrap_service._ADMIN_DEFAULT_PERMISSIONS`` so ADMIN can use
the new endpoints out of the box.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.dependencies.db import get_db
from app.dependencies.rbac import require_permission
from app.schemas.reports import (
    ReportDefinitionCreateRequest,
    ReportDefinitionResponse,
    ReportRunResponse,
    ReportRunWithSnapshotResponse,
    ReportSnapshotResponse,
)
from database.models.app_user import AppUser
from services import report_service

router = APIRouter(tags=["reports"])

REPORT_MANAGE_PERMISSION_CODE = "REPORT_MANAGE"
_require_report_manage = require_permission(REPORT_MANAGE_PERMISSION_CODE)


@router.post(
    "/report-definitions",
    response_model=ReportDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a report definition",
)
def create_report_definition(
    body: ReportDefinitionCreateRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_report_manage),
) -> ReportDefinitionResponse:
    try:
        rd = report_service.create_report_definition(
            db,
            report_type_id=body.report_type_id,
            owner_user_id=current_user.id,
            name=body.name,
            parameters=body.parameters,
            output_format=body.output_format,
            schedule_cron=body.schedule_cron,
            actor_id=current_user.id,
        )
    except report_service.InvalidOutputFormatError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except report_service.DuplicateReportDefinitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(rd)
    return ReportDefinitionResponse.model_validate(rd)


@router.get(
    "/report-definitions/{report_definition_id}",
    response_model=ReportDefinitionResponse,
    summary="Get a report definition",
)
def read_report_definition(
    report_definition_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_report_manage),
) -> ReportDefinitionResponse:
    try:
        rd = report_service.get_report_definition(db, report_definition_id)
    except report_service.ReportDefinitionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ReportDefinitionResponse.model_validate(rd)


@router.post(
    "/report-definitions/{report_definition_id}/run",
    response_model=ReportRunWithSnapshotResponse,
    summary="Run a report (synchronous, returns snapshot data inline)",
)
def run_report(
    report_definition_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(_require_report_manage),
) -> ReportRunWithSnapshotResponse:
    try:
        run = report_service.run_report(
            db,
            report_definition_id=report_definition_id,
            triggered_by=current_user.id,
        )
    except report_service.ReportDefinitionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except report_service.ReportBuilderNotImplementedError as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except report_service.ReportRunFailedError as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    db.commit()
    db.refresh(run)

    # Fetch the snapshot if COMPLETE.
    snapshot = report_service.get_report_snapshot(db, run.id)

    return ReportRunWithSnapshotResponse(
        run=ReportRunResponse.model_validate(run),
        snapshot=ReportSnapshotResponse.model_validate(snapshot) if snapshot else None,
    )


@router.get(
    "/report-runs/{report_run_id}",
    response_model=ReportRunWithSnapshotResponse,
    summary="Get a report run (status + snapshot if COMPLETE)",
)
def read_report_run(
    report_run_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: AppUser = Depends(_require_report_manage),
) -> ReportRunWithSnapshotResponse:
    try:
        run = report_service.get_report_run(db, report_run_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    snapshot = report_service.get_report_snapshot(db, run.id)

    return ReportRunWithSnapshotResponse(
        run=ReportRunResponse.model_validate(run),
        snapshot=ReportSnapshotResponse.model_validate(snapshot) if snapshot else None,
    )


__all__ = ["router"]
