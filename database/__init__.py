"""Database package for the Enterprise ERP (SIWRMS).

This package provides the SQLAlchemy/database foundation for the ERP
application:

* ``database.base``     — the declarative ``Base`` class and shared ``MetaData``.
* ``database.session``  — engine / sessionmaker construction.
* ``database.naming``   — the project's constraint/index naming convention.
* ``database.types``    — custom Numeric/String column-type factories.
* ``database.mixins``   — the UAC / AAC audit-column mixins.
* ``database.constants``— project-wide schema, role, and precision constants.
* ``database.models``   — the implemented domain models (78 modules).

This top-level ``__init__`` module itself only re-exports the project-wide
constants and the ``constants`` sub-module; the other sub-modules above are
imported directly by their own path (e.g. ``from database.base import Base``)
so that the package can be used without pulling in drivers that may not be
installed in every environment (e.g. psycopg during linting).

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
