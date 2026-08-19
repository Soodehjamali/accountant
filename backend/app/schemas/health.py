"""Response schemas for the health-check endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Response body for ``GET /health`` -- process-level liveness only."""

    model_config = ConfigDict(json_schema_extra={"example": {"status": "ok"}})

    status: str


class DatabaseHealthResponse(BaseModel):
    """Response body for ``GET /health/db`` -- PostgreSQL connectivity."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"status": "ok", "database": "connected", "detail": None}
        }
    )

    status: str
    database: str
    detail: str | None = None


__all__ = ["HealthResponse", "DatabaseHealthResponse"]
