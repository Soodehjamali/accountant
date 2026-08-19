"""Health-check endpoints.

Two distinct checks, deliberately kept separate:

* ``/health``       -- process liveness only. No DB access. Answers
  "is the process up and able to handle a request at all".
* ``/health/db``    -- PostgreSQL connectivity check (requirement #8).
  Executes a trivial ``SELECT 1`` against the existing
  ``database.session`` engine. Answers "can this process actually reach
  its database right now".

Both are exposed twice: once unversioned at the app root (``/health``,
for infra probes that should not depend on API versioning), and once
under ``/api/v1`` (this module, mounted by api/v1/router.py) for API
consumers that want a versioned contract.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.schemas.health import DatabaseHealthResponse, HealthResponse
from database.session import get_engine

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse, summary="Liveness check")
def health() -> HealthResponse:
    """Process liveness only -- does not touch the database."""

    return HealthResponse(status="ok")


@router.get(
    "/db",
    response_model=DatabaseHealthResponse,
    summary="Database connectivity check",
)
def health_db() -> DatabaseHealthResponse:
    """Verify PostgreSQL connectivity via a trivial ``SELECT 1``.

    Never raises to the client on a DB failure -- returns a 200 with
    ``status="error"`` and the exception detail instead, so this endpoint
    itself stays reliable as a diagnostic even when the database is down.
    """

    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return DatabaseHealthResponse(status="ok", database="connected")
    except Exception as exc:  # noqa: BLE001 - deliberate: report, don't crash
        return DatabaseHealthResponse(
            status="error", database="unreachable", detail=str(exc)
        )


__all__ = ["router"]
