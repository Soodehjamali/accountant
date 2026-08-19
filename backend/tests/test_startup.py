"""Application startup tests.

Verifies the app can be constructed and that FastAPI's own OpenAPI/Swagger
endpoints (requirement #10) are present -- not a business test, purely
"does the app boot and expose the docs it's supposed to".
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_app_starts_and_exposes_openapi_schema(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"]


def test_swagger_ui_is_served(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_redoc_is_served(client: TestClient) -> None:
    response = client.get("/redoc")
    assert response.status_code == 200
