"""Tests for the liveness health endpoint (requirement #7).

Deliberately does not touch the database -- see test_db_health.py for
the DB connectivity check.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_root_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_versioned_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
