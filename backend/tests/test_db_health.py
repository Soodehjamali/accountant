"""Tests for the database connectivity health check (requirement #8).

Skipped automatically if DATABASE_URL is not configured in the test
environment -- this test verifies real connectivity, it does not mock
the database, so it needs a live PostgreSQL to be meaningful.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB connectivity test",
)


@requires_database
def test_db_health_reports_connected(client: TestClient) -> None:
    response = client.get("/health/db")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


@requires_database
def test_versioned_db_health_reports_connected(client: TestClient) -> None:
    response = client.get("/api/v1/health/db")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


def test_db_health_endpoint_never_raises_even_if_unreachable(
    client: TestClient,
) -> None:
    """Regardless of DB state, the endpoint itself must return HTTP 200."""

    response = client.get("/health/db")
    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "error"}
