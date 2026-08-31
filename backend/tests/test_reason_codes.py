"""Tests for the reason code endpoints: ``GET /reason-codes``.

Skipped automatically if ``DATABASE_URL`` is not configured.
Tests the read-only reason code catalog endpoint.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from database.session import get_session_factory
from services import auth_service, bootstrap_service

requires_database = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL is not set; skipping live DB reason code tests",
)


def _login(username: str, password: str) -> dict[str, str]:
    from app.core.config import get_settings
    from security import create_access_token

    settings = get_settings()
    session = get_session_factory()()
    try:
        user = auth_service.authenticate_user(
            session, username_or_email=username, password=password
        )
        assert user is not None
        session.commit()
        token = create_access_token(
            subject=str(user.id),
            secret_key=settings.secret_key,
            expires_in_seconds=settings.access_token_expire_minutes * 60,
        )
    finally:
        session.close()
    return {"Authorization": f"Bearer {token}"}


def _make_user() -> dict[str, str]:
    """Create a basic authenticated user (no special permissions needed for reads)."""
    session = get_session_factory()()
    try:
        bootstrap_service.ensure_rbac_bootstrap(session)
        system_user = bootstrap_service.ensure_system_user(session)
        suffix = uuid.uuid4().hex[:8]
        username = f"test_rc_{suffix}"
        password = "correct-horse-battery-staple"
        auth_service.create_user(
            session,
            username=username,
            email=f"{username}@example.invalid",
            password=password,
            created_by=system_user.id,
        )
        session.commit()
    finally:
        session.close()
    return _login(username, password)


@requires_database
def test_list_reason_codes_returns_seeded_codes(
    client: TestClient,
) -> None:
    """GET /reason-codes returns the seeded PRICING_ERROR reason code."""
    headers = _make_user()

    # Ensure at least the seeded reason code exists
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        bootstrap_service.ensure_default_reason_code(session, actor_id=system_user.id)
        session.commit()
    finally:
        session.close()

    resp = client.get("/api/v1/reason-codes", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    codes = [item["code"] for item in items]
    assert "PRICING_ERROR" in codes


@requires_database
def test_list_reason_codes_filter_by_scope(
    client: TestClient,
) -> None:
    """GET /reason-codes?scope=RETURN filters to RETURN-scoped codes only."""
    headers = _make_user()

    # Ensure seeded reason code exists (scope=ADJUSTMENT)
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        bootstrap_service.ensure_default_reason_code(session, actor_id=system_user.id)
        session.commit()
    finally:
        session.close()

    # Filter by RETURN scope — should not include PRICING_ERROR (scope=ADJUSTMENT)
    resp = client.get("/api/v1/reason-codes?scope=RETURN", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    for item in items:
        assert item["scope"] == "RETURN"
    codes = [item["code"] for item in items]
    assert "PRICING_ERROR" not in codes


@requires_database
def test_list_reason_codes_unauthenticated_returns_401(
    client: TestClient,
) -> None:
    """GET /reason-codes without auth returns 401."""
    resp = client.get("/api/v1/reason-codes")
    assert resp.status_code == 401


# ------------------------------------------------------------------
# Regression tests: every scope must have at least one seeded code
# ------------------------------------------------------------------


def _seed_all_reason_codes() -> None:
    """Idempotently seed all bootstrap reason codes (all scopes)."""
    session = get_session_factory()()
    try:
        system_user = bootstrap_service.ensure_system_user(session)
        bootstrap_service.ensure_reason_codes(session, actor_id=system_user.id)
        session.commit()
    finally:
        session.close()


@requires_database
def test_return_scope_has_seeded_codes(
    client: TestClient,
) -> None:
    """Regression: GET /reason-codes?scope=RETURN must return seeded codes.

    This is the test that should have caught the empty-dropdown bug
    in CreditNoteCreatePage -- the frontend queries scope=RETURN and
    the seed data had no RETURN-scoped codes.
    """
    headers = _make_user()
    _seed_all_reason_codes()

    resp = client.get("/api/v1/reason-codes?scope=RETURN", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1, (
        "GET /reason-codes?scope=RETURN returned empty -- "
        "CreditNoteCreatePage dropdown would have no options"
    )
    codes = {item["code"] for item in items}
    # At least the two canonical RETURN codes from bootstrap seed
    assert "DAMAGED_GOODS" in codes
    assert "WRONG_ITEM_SHIPPED" in codes
    for item in items:
        assert item["scope"] == "RETURN"


@requires_database
def test_variance_scope_has_seeded_code(
    client: TestClient,
) -> None:
    """Regression: GET /reason-codes?scope=VARIANCE must return at least one code.

    Needed by the upcoming Inventory milestone (stock_adjustment /
    physical_count reference reason_code_ref with VARIANCE scope).
    """
    headers = _make_user()
    _seed_all_reason_codes()

    resp = client.get("/api/v1/reason-codes?scope=VARIANCE", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1, (
        "GET /reason-codes?scope=VARIANCE returned empty -- "
        "Inventory milestone would hit empty dropdown"
    )
    codes = {item["code"] for item in items}
    assert "COUNT_VARIANCE" in codes
    for item in items:
        assert item["scope"] == "VARIANCE"


@requires_database
def test_damage_scope_has_seeded_code(
    client: TestClient,
) -> None:
    """Regression: GET /reason-codes?scope=DAMAGE must return at least one code.

    Needed by the upcoming Inventory milestone (DAMAGED_OUT movement
    type references reason_code_ref with DAMAGE scope).
    """
    headers = _make_user()
    _seed_all_reason_codes()

    resp = client.get("/api/v1/reason-codes?scope=DAMAGE", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1, (
        "GET /reason-codes?scope=DAMAGE returned empty -- "
        "Inventory milestone would hit empty dropdown"
    )
    codes = {item["code"] for item in items}
    assert "SCRAP_GOODS" in codes
    for item in items:
        assert item["scope"] == "DAMAGE"


@requires_database
def test_all_scopes_have_seeded_codes(
    client: TestClient,
) -> None:
    """Regression: every valid scope has at least one seeded reason code.

    Catches future regressions where a scope loses its last seeded code.
    """
    headers = _make_user()
    _seed_all_reason_codes()

    for scope in ("ADJUSTMENT", "VARIANCE", "RETURN", "DAMAGE"):
        resp = client.get(
            f"/api/v1/reason-codes?scope={scope}", headers=headers
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 1, (
            f"GET /reason-codes?scope={scope} returned empty after bootstrap"
        )
