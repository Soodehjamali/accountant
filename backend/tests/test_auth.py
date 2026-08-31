"""Tests for the auth endpoints: ``POST /auth/login``, ``GET /auth/me``.

Skipped automatically if ``DATABASE_URL`` is not configured in the test
environment (same convention as ``test_db_health.py``) -- these exercise
the real ``services.auth_service`` against a real database, not a mock.

Each test that needs a logged-in user creates one directly via
``services.auth_service.create_user`` (a random ``test_auth_<hex>``
username/email per run, so re-running this file against a persistent dev
database never collides on the unique username/email constraints) -- there
is no public self-registration HTTP endpoint yet (out of scope for this
task; see ``11_MVP_Implementation_Plan.md``, Phase 1).
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database.models.representative import Representative
from database.session import get_session_factory
from services import auth_service, bootstrap_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB auth tests",
)


@pytest.fixture()
def seeded_user() -> tuple[str, str]:
    """Create a fresh, ``ACTIVE`` ``AppUser`` and return ``(username, password)``."""

    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_auth_{suffix}"
        password = "correct-horse-battery-staple"
        auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )
        session.commit()
        yield username, password
    finally:
        session.close()


@requires_database
def test_login_with_correct_credentials_returns_token(
    client: TestClient, seeded_user: tuple[str, str]
) -> None:
    username, password = seeded_user
    response = client.post(
        "/api/v1/auth/login",
        json={"username_or_email": username, "password": password},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["access_token"]


@requires_database
def test_login_with_wrong_password_returns_401(
    client: TestClient, seeded_user: tuple[str, str]
) -> None:
    username, _password = seeded_user
    response = client.post(
        "/api/v1/auth/login",
        json={"username_or_email": username, "password": "wrong-password"},
    )
    assert response.status_code == 401


@requires_database
def test_login_with_unknown_user_returns_401(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username_or_email": "no-such-user-at-all", "password": "whatever"},
    )
    assert response.status_code == 401


@requires_database
def test_me_without_token_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


@requires_database
def test_me_with_valid_token_returns_profile(
    client: TestClient, seeded_user: tuple[str, str]
) -> None:
    username, password = seeded_user
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username_or_email": username, "password": password},
    )
    token = login_response.json()["access_token"]

    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == username
    assert "password" not in body
    assert "password_hash" not in body
    assert body["portal"] == "office"


@requires_database
def test_me_portal_office_for_non_representative_user(
    client: TestClient, seeded_user: tuple[str, str]
) -> None:
    """A user without a representative_id gets portal='office'."""
    username, password = seeded_user
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username_or_email": username, "password": password},
    )
    token = login_response.json()["access_token"]

    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["portal"] == "office"
    assert "representative_id" not in body


@requires_database
def test_me_portal_representative_for_linked_user(
    client: TestClient, seeded_user: tuple[str, str]
) -> None:
    """A user linked to a Representative gets portal='representative'."""
    username, password = seeded_user
    session = get_session_factory()()
    try:
        # Create a representative to link to.
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        rep = Representative(
            code=f"REP-AUTH-{suffix.upper()}",
            person_name=f"Auth Test Rep {suffix}",
            status="ACTIVE",
            created_by=system_user.id,
            updated_by=system_user.id,
        )
        session.add(rep)
        session.flush()

        # Create a user linked to that representative.
        rep_suffix = uuid.uuid4().hex[:8]
        rep_username = f"test_auth_rep_{rep_suffix}"
        auth_service.create_user(
            session,
            username=rep_username,
            email=f"{rep_username}@example.invalid",
            password=password,
            created_by=system_user.id,
            representative_id=rep.id,
        )
        session.commit()
    finally:
        session.close()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"username_or_email": rep_username, "password": password},
    )
    token = login_response.json()["access_token"]

    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["portal"] == "representative"
    assert "representative_id" not in body


@requires_database
def test_me_with_garbage_token_returns_401(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
