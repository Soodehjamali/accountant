"""Alembic environment for the Enterprise ERP (SIWRMS) database.

Responsibilities of this file (per the task's own requirements):

1. Bind ``target_metadata`` to :data:`database.base.Base.metadata` -- the
   single, project-wide ``MetaData`` instance (schema ``erp``, spec-mandated
   naming convention) every one of this codebase's 78 ORM models registers
   its table against.
2. Import all 78 concrete model modules so each one's ``__tablename__`` /
   columns / constraints actually populate ``Base.metadata`` before Alembic
   compares it against the live database -- an unimported model is
   invisible to ``Base.metadata`` and would be silently DROPped by
   autogenerate (SQLAlchemy only registers a table on the metadata when the
   Python module defining that mapped class has actually been executed).
3. Read the connection string from the ``DATABASE_URL`` environment
   variable at runtime -- never hardcoded here or in ``alembic.ini``.

Model import list -- transcribed verbatim, same order, from the project's
own ``database/models/check_mappers.py`` (the authoritative, currently
green, FK-dependency-ordered import list for all 78 models), rather than
``from database.models.models_init import *`` -- ``models_init.py``'s own
module docstring states it "intentionally re-exports nothing by name yet
(no eager model loading here)" precisely so that importing the package
itself stays cheap and side-effect-free; it is not designed to be the
mechanism that populates ``Base.metadata``. ``check_mappers.py`` is exactly
the opposite: its entire purpose is eagerly importing every model so
``configure_mappers()`` can run and ``Base.metadata`` is fully populated --
the same property this file needs from Alembic's own migration-generation
process. The import order itself does not affect SQLAlchemy's ability to
resolve string-based ``ForeignKey("target_table.id")`` references (those
resolve lazily, by table name, against the fully-populated
``Base.metadata`` at ``configure_mappers()``/mapper-configuration time, not
at class-definition time) -- but the order is kept identical to
``check_mappers.py`` regardless, purely so the two files never visibly
diverge and so any future table addition only has to be appended in one
place mentally, not reconciled between two different orderings.

IMPORTANT -- the ``erp`` schema itself:
    ``Base.metadata`` is bound to ``schema="erp"`` (see ``database/base.py``),
    so every ``CREATE TABLE`` Alembic generates already carries the
    ``erp.`` prefix automatically. Autogenerate does **not**, however, ever
    emit a ``CREATE SCHEMA`` statement for a schema referenced only by table
    metadata -- Alembic's autogenerate diffs objects *within* schemas it is
    told to inspect (via ``include_schemas=True`` below), it does not
    create the schemas themselves. The very first migration against a fresh
    database therefore needs an explicit ``op.execute("CREATE SCHEMA IF NOT
    EXISTS erp")`` as its first operation -- flagged here, and again in the
    initial migration's own review notes, so this isn't missed.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Make the project root importable (so `import database...` below works
# regardless of the current working directory Alembic is invoked from).
# alembic.ini's own `prepend_sys_path = .` already covers the common case
# (running `alembic` from the project root), this is defense-in-depth for
# less common invocations (e.g. from within migrations/ itself, or via an
# absolute -c path to alembic.ini from elsewhere).
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Optional .env support -- python-dotenv is in requirements.txt precisely so
# DATABASE_URL can live in a local, gitignored .env file during development
# instead of always being exported in the shell. Loading it here is a no-op
# (and does not error) if no .env file is present, or if the person prefers
# to export the variable directly -- an explicit shell-exported DATABASE_URL
# always wins either way, since load_dotenv() by default does not override
# already-set environment variables.
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a listed dependency,
    # but env.py should not hard-fail if it's transiently missing from a
    # given environment (e.g. a minimal CI image that exports DATABASE_URL
    # directly and never installs the optional dotenv convenience).
    pass

# ---------------------------------------------------------------------------
# Alembic Config object -- gives access to values within alembic.ini.
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging (per alembic.ini's own
# [loggers]/[handlers]/[formatters] sections).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Import Base and every one of the 78 models -- see module docstring.
# Transcribed verbatim from database/models/check_mappers.py's own import
# block (same order).
# ---------------------------------------------------------------------------
from database.base import Base  # noqa: E402

from database.models.app_user import AppUser  # noqa: E402,F401
from database.models.approval_request import ApprovalRequest  # noqa: E402,F401
from database.models.approval_history import ApprovalHistory  # noqa: E402,F401
from database.models.attachment import Attachment  # noqa: E402,F401
from database.models.audit_log import AuditLog  # noqa: E402,F401
from database.models.bot_platform_ref import BotPlatformRef  # noqa: E402,F401
from database.models.carrier import Carrier  # noqa: E402,F401
from database.models.city_ref import CityRef  # noqa: E402,F401
from database.models.commission_config import CommissionConfig  # noqa: E402,F401
from database.models.costing_method_config import CostingMethodConfig  # noqa: E402,F401
from database.models.currency import Currency  # noqa: E402,F401
from database.models.customer import Customer  # noqa: E402,F401
from database.models.customer_contact import CustomerContact  # noqa: E402,F401
from database.models.customer_rep_assignment import CustomerRepAssignment  # noqa: E402,F401
from database.models.bot_binding_token import BotBindingToken  # noqa: E402,F401
from database.models.bot_session import BotSession  # noqa: E402,F401
from database.models.bot_message_log import BotMessageLog  # noqa: E402,F401
from database.models.customer_ledger import CustomerLedger  # noqa: E402,F401
from database.models.customer_ledger_entry import CustomerLedgerEntry  # noqa: E402,F401
from database.models.discount import Discount  # noqa: E402,F401
from database.models.generated_document import GeneratedDocument  # noqa: E402,F401
from database.models.inventory_balance_snapshot import InventoryBalanceSnapshot  # noqa: E402,F401
from database.models.inventory_transaction import InventoryTransaction  # noqa: E402,F401
from database.models.invoice import Invoice  # noqa: E402,F401
from database.models.invoice_line import InvoiceLine  # noqa: E402,F401
from database.models.invoice_history import InvoiceHistory  # noqa: E402,F401
from database.models.movement_type_ref import MovementTypeRef  # noqa: E402,F401
from database.models.notification_type_ref import NotificationTypeRef  # noqa: E402,F401
from database.models.order import Order  # noqa: E402,F401
from database.models.invoice_order import InvoiceOrder  # noqa: E402,F401
from database.models.order_line import OrderLine  # noqa: E402,F401
from database.models.order_price_freeze import OrderPriceFreeze  # noqa: E402,F401
from database.models.order_status_history import OrderStatusHistory  # noqa: E402,F401
from database.models.payment import Payment  # noqa: E402,F401
from database.models.payment_allocation import PaymentAllocation  # noqa: E402,F401
from database.models.permission import Permission  # noqa: E402,F401
from database.models.physical_count import PhysicalCount  # noqa: E402,F401
from database.models.physical_count_line import PhysicalCountLine  # noqa: E402,F401
from database.models.price_history import PriceHistory  # noqa: E402,F401
from database.models.price_list import PriceList  # noqa: E402,F401
from database.models.product import Product  # noqa: E402,F401
from database.models.product_category import ProductCategory  # noqa: E402,F401
from database.models.product_image import ProductImage  # noqa: E402,F401
from database.models.product_lot import ProductLot  # noqa: E402,F401
from database.models.product_serial import ProductSerial  # noqa: E402,F401
from database.models.purchase_price_history import PurchasePriceHistory  # noqa: E402,F401
from database.models.reason_code_ref import ReasonCodeRef  # noqa: E402,F401
from database.models.credit_note import CreditNote  # noqa: E402,F401
from database.models.credit_note_line import CreditNoteLine  # noqa: E402,F401
from database.models.report_definition import ReportDefinition  # noqa: E402,F401
from database.models.report_run import ReportRun  # noqa: E402,F401
from database.models.report_type_ref import ReportTypeRef  # noqa: E402,F401
from database.models.representative import Representative  # noqa: E402,F401
from database.models.commission_transaction import CommissionTransaction  # noqa: E402,F401
from database.models.credit_limit_config import CreditLimitConfig  # noqa: E402,F401
from database.models.notification import Notification  # noqa: E402,F401
from database.models.notification_history import NotificationHistory  # noqa: E402,F401
from database.models.report_snapshot import ReportSnapshot  # noqa: E402,F401
from database.models.representative_contact import RepresentativeContact  # noqa: E402,F401
from database.models.role import Role  # noqa: E402,F401
from database.models.role_permission import RolePermission  # noqa: E402,F401
from database.models.shipment import Shipment  # noqa: E402,F401
from database.models.shipment_line import ShipmentLine  # noqa: E402,F401
from database.models.shipment_status_history import ShipmentStatusHistory  # noqa: E402,F401
from database.models.stock_adjustment import StockAdjustment  # noqa: E402,F401
from database.models.stock_reservation import StockReservation  # noqa: E402,F401
from database.models.stock_transfer import StockTransfer  # noqa: E402,F401
from database.models.system_config import SystemConfig  # noqa: E402,F401
from database.models.transfer_history import TransferHistory  # noqa: E402,F401
from database.models.transfer_line import TransferLine  # noqa: E402,F401
from database.models.unit_of_measure import UnitOfMeasure  # noqa: E402,F401
from database.models.uom_conversion import UomConversion  # noqa: E402,F401
from database.models.user_role import UserRole  # noqa: E402,F401
from database.models.warehouse import Warehouse  # noqa: E402,F401
from database.models.customer_return import CustomerReturn  # noqa: E402,F401
from database.models.return_line import ReturnLine  # noqa: E402,F401
from database.models.kpi_snapshot import KpiSnapshot  # noqa: E402,F401
from database.models.warehouse_assignment import WarehouseAssignment  # noqa: E402,F401
from database.models.warehouse_location import WarehouseLocation  # noqa: E402,F401

# ---------------------------------------------------------------------------
# target_metadata -- the single source of truth Alembic diffs against.
# ---------------------------------------------------------------------------
target_metadata = Base.metadata


def get_url() -> str:
    """Read the database connection string from ``DATABASE_URL``.

    Deliberately raises rather than falling back to any hardcoded default
    (including ``alembic.ini``'s own placeholder ``sqlalchemy.url``) -- a
    silent fallback to a fake/local-looking connection string is exactly
    the kind of mistake that could point a migration at the wrong database
    (or fail confusingly deep inside SQLAlchemy's connection pool instead
    of with a clear, actionable message at startup).
    """

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. Set it to a "
            "SQLAlchemy-style connection string, e.g.:\n"
            "  postgresql+psycopg2://USER:PASSWORD@HOST:5432/DBNAME\n"
            "either by exporting it in the shell or by adding it to a "
            ".env file in the project root (python-dotenv, already a "
            "project dependency, loads that file automatically)."
        )
    return url


# Override whatever alembic.ini's own [alembic] sqlalchemy.url happens to
# say (see that file's own comment on this) with the real, environment-
# sourced URL, for both the offline and online code paths below.
config.set_main_option("sqlalchemy.url", get_url())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine, though
    an Engine is acceptable here as well. By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the script
    output.
    """

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Every table in target_metadata already carries schema="erp" via
        # database/base.py's own MetaData(schema=APP_SCHEMA) -- this just
        # tells Alembic's own comparison/rendering logic to consider
        # non-default schemas at all, rather than assuming everything
        # lives in "public". See this file's own module docstring for why
        # this does NOT itself emit `CREATE SCHEMA erp`.
        include_schemas=True,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine and associate a connection
    with the context.
    """

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
