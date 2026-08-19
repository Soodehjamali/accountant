"""Shared pytest fixtures for the backend test suite.

Adds both the repository root (so ``import database...`` resolves) and
``backend/`` itself (so ``import app...`` resolves) to ``sys.path`` --
mirrors how ``alembic.ini``'s own ``prepend_sys_path`` handles the same
concern for migrations/env.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

for path in (str(REPO_ROOT), str(BACKEND_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.main import create_app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    """A TestClient bound to a freshly constructed app instance."""

    app = create_app()
    return TestClient(app)


__all__ = ["client"]
