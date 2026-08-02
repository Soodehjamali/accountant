"""Project-wide database constants for the Enterprise ERP (SIWRMS).

These constants encode the architecture- and database-spec-level decisions that
are *fixed* across the entire persistence layer (engine target, schema, roles,
and the numeric/string precision contracts used by every column). They have no
SQLAlchemy dependency and are importable in any environment. Naming-convention
behavior (prefix vocabulary and DDL object-name construction) lives in
``database/naming.py``; custom column types live in ``database/types.py``;
neither is duplicated here.

Authority:
    - docs/06_ERD.md          (~0.2 Universal Audit Fields, ~0.5 Naming)
    - docs/07_DATABASE_SPEC.md (UAC / AAC column contracts, type conventions)
    - docs/08_Architecture.md
"""

from __future__ import annotations

from enum import Enum
from typing import Final

# ---------------------------------------------------------------------------
# Engine / dialect target
# ---------------------------------------------------------------------------
#: SQLAlchemy 2.x targeting PostgreSQL 17 (docs/01_Project_Vision.md,
#  docs/07_DATABASE_SPEC.md: TIMESTAMPTZ, JSONB, INET, gen_random_uuid()).
POSTGRES_DIALECT_NAME: Final[str] = "postgresql"
POSTGRES_MAJOR_VERSION: Final[int] = 17


class DatabaseDialect(str, Enum):
    """Supported SQL dialect identifiers mapped to SQLAlchemy URL drivers.

    Mirrors the architecture's PostgreSQL 17 target; the psycopg3 driver
    variant is the preferred connector. The ``str`` mixin makes every member a
    usable ``str`` so it can be embedded directly in a SQLAlchemy URL, e.g.
    ``f"{DatabaseDialect.POSTGRESQL}+psycopg"``. Strict-mode compatible.
    """

    POSTGRESQL = "postgresql"
    POSTGRESQL_PSYCOPG2 = "postgresql+psycopg2"
    POSTGRESQL_PSYCOPG3 = "postgresql+psycopg"


# ---------------------------------------------------------------------------
# Default connection / runtime behavior
# ---------------------------------------------------------------------------
#: Timezone used for every UTC-bound audit column (ERD ~0.2 timestamps are UTC).
UTC_TIMEZONE: Final[str] = "UTC"

#: Server-side statement timeout applied as a sane per-query default (ms).
DEFAULT_STATEMENT_TIMEOUT_MS: Final[int] = 30_000

#: Server-side fetch size for streaming ``yield_per`` cursors (rows).
DEFAULT_FETCH_SIZE: Final[int] = 1_000

#: Default application pool size; overridden by connection URL / config.
DEFAULT_POOL_SIZE: Final[int] = 10

#: Maximum connection-pool overflow before raising pool-exhausted errors.
DEFAULT_MAX_OVERFLOW: Final[int] = 20

#: Connection pool pre-ping enablement (cheap liveness check per checkout).
DEFAULT_POOL_PRE_PING: Final[bool] = True


# ---------------------------------------------------------------------------
# Schema & roles (GRANT posture documented throughout docs/07_DATABASE_SPEC.md)
# ---------------------------------------------------------------------------
#: Primary application schema. All domain objects live here.
APP_SCHEMA: Final[str] = "erp"

#: Standard application role used by services for ordinary CRUD.
#: (UAC tables; perms are revoked for append-only / non-authoritative tables.)
APP_ROLE: Final[str] = "app_role"

#: Restricted reconciliation / projection role that owns cache writes
#: (invoice.amount_paid, payment.unallocated_amount, customer_ledger,
#:  inventory_balance_snapshot, kpi_snapshot) — column-level GRANT only.
RECONCILIATION_ROLE: Final[str] = "reconciliation_role"

#: Reporting / scheduler role permitted to INSERT into append-only snapshot and
#: KPI tables from lawful background jobs (audit_log, kpi_snapshot, ...).
REPORTING_ROLE: Final[str] = "reporting_role"


# ---------------------------------------------------------------------------
# Numeric precision contracts (docs/07_DATABASE_SPEC.md, repeated column types)
# ---------------------------------------------------------------------------
class NumericPrecision:
    """Canonical ``NUMERIC(p, s)`` precisions used across the schema.

    Stored as ``Final``-typed class attributes (MyPy-strict) so that domain
    columns reference a single source of truth rather than magic numbers.

    Examples from the spec:
        signed_quantity   NUMERIC(18, 4)
        unit_cost         NUMERIC(18, 6)
        unit_price        NUMERIC(18, 4)
        tax_rate          NUMERIC(7,  4)
        lat / lng         NUMERIC(9,  6)
        grand_total etc.  NUMERIC(18, 4)
    """

    #: Money / quantities with four decimals (money totals, transaction qty).
    PRECISION_MONEY: Final[int] = 18
    SCALE_MONEY: Final[int] = 4

    #: Snapshot costs at six-decimal precision (unit_cost, unit_cost_at_ship).
    PRECISION_COST: Final[int] = 18
    SCALE_COST: Final[int] = 6

    #: Rate percentages with four decimals, bounded 0..100 (tax_rate,
    #: commission rate, discount percent).
    PRECISION_RATE: Final[int] = 7
    SCALE_RATE: Final[int] = 4

    #: Geo-tracking lat/lng (lat NUMERIC(9,6), lng NUMERIC(9,6)).
    PRECISION_GEO: Final[int] = 9
    SCALE_GEO: Final[int] = 6


# ---------------------------------------------------------------------------
# String length contracts (= VARCHAR(N) business keys / states / channels)
# ---------------------------------------------------------------------------
class StringLength:
    """Canonical ``VARCHAR(N)`` lengths used across the schema.

    Derived from repeated column definitions in docs/07_DATABASE_SPEC.md so
    the domain layer imports widths rather than re-hard-coding them.
    """

    #: Human display name (e.g. product.name, warehouse.name, full names).
    NAME: Final[int] = 160

    #: Primary business-document key (order/transfer/invoice/etc. numbers).
    BUSINESS_KEY: Final[int] = 40

    #: Short state / channel / type token (states, channels, small enums).
    STATE_TOKEN: Final[int] = 16

    #: Extended state token (TransferState / OrderState use up to 24 chars).
    STATE_TOKEN_LONG: Final[int] = 24

    #: Polymorphic / enum-style tokens with room for growth.
    TYPE_TOKEN: Final[int] = 40

    #: Short code (~12 chars), e.g. SKU / warehouse code / currency ISO-3.
    CODE_SHORT: Final[int] = 40

    #: Ephemeral tokens (session tokens, tracking numbers, bank references).
    TOKEN: Final[int] = 120

    #: Tracking number (carrier-specific, may be longer).
    TRACKING_NUMBER: Final[int] = 80

    #: Description / line description.
    DESCRIPTION: Final[int] = 255

    #: Storage key / URI (S3-compatible object keys).
    STORAGE_KEY: Final[int] = 512

    #: Mime type / format token.
    MIME_TYPE: Final[int] = 120

    #: Cron expression length (report schedules).
    CRON_EXPRESSION: Final[int] = 60


#: PostgreSQL's hard identifier-length limit (NAMEDATALEN=64 internally,
#: i.e. 63 usable characters before silent truncation). Any generated
#: constraint/index name must be enforced to this ceiling — see
#: database/naming.py's ``_enforce_length_limit``.
POSTGRES_IDENTIFIER_MAX_LENGTH: Final[int] = 63

#: Hash-chain column width (SHA-256 hex stored as CHAR(64)).
#: (inventory_transaction/customer_ledger_entry/commission row_hash/prev_hash,
#:  attachment checksum) — docs/07_DATABASE_SPEC.md.
HASH_HEX_LENGTH: Final[int] = 64


#: Default optimistic-lock starting value for the ``version`` UAC column.
#: (ERD ~0.2: "version integer / row-version optimistic concurrency token".)
OPTIMISTIC_LOCK_VERSION_START: Final[int] = 1


__all__ = [
    "APP_ROLE",
    "APP_SCHEMA",
    "DEFAULT_FETCH_SIZE",
    "DEFAULT_MAX_OVERFLOW",
    "DEFAULT_POOL_PRE_PING",
    "DEFAULT_POOL_SIZE",
    "DEFAULT_STATEMENT_TIMEOUT_MS",
    "DatabaseDialect",
    "HASH_HEX_LENGTH",
    "NumericPrecision",
    "OPTIMISTIC_LOCK_VERSION_START",
    "POSTGRES_DIALECT_NAME",
    "POSTGRES_IDENTIFIER_MAX_LENGTH",
    "POSTGRES_MAJOR_VERSION",
    "RECONCILIATION_ROLE",
    "REPORTING_ROLE",
    "StringLength",
    "UTC_TIMEZONE",
]
