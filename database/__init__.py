"""Database package for the Enterprise ERP (SIWRMS).

This package provides the SQLAlchemy foundation required before any domain
model is implemented. Only the public foundation surface is exported here; no
business models, repositories, sessions, mixins, or Alembic migrations are
created at this stage.

Public symbols are intentionally limited to the project-wide constants and the
declarative base / metadata that downstream domain models will build on. Each
sub-module is imported lazily so that the package can be used without pulling
in drivers that may not be installed in every environment (e.g. psycopg during
linting).

See:
    - docs/06_ERD.md          (~0.2 Universal Audit Fields, ~0.5 Naming)
    - docs/07_DATABASE_SPEC.md (UAC / AAC column contracts, type conventions)
"""

from __future__ import annotations

from . import constants
from database.constants import (
    APP_ROLE,
    APP_SCHEMA,
    DEFAULT_FETCH_SIZE,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    RECONCILIATION_ROLE,
    UTC_TIMEZONE,
    DatabaseDialect,
    NumericPrecision,
    StringLength,
)

__all__ = [
    # Sub-modules
    "constants",
    # Database engine / schema configuration
    "DatabaseDialect",
    "APP_SCHEMA",
    "APP_ROLE",
    "RECONCILIATION_ROLE",
    "DEFAULT_FETCH_SIZE",
    "DEFAULT_STATEMENT_TIMEOUT_MS",
    "UTC_TIMEZONE",
    # Naming conventions
    # Type precision contracts
    "NumericPrecision",
    "StringLength",
]

__version__ = "0.1.0"
