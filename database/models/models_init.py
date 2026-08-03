"""ORM models package for the Enterprise ERP (SIWRMS).

Concrete SQLAlchemy 2.x ORM models for the application. Each model lives in its
own module and declares a single ERD/entity table against the shared
:class:`database.base.Base` (which is bound to the project ``MetaData`` that
carries :data:`database.naming.NAMING_CONVENTION`).

Submodules (audited complete list -- every module under ``database/models/``
except this ``__init__.py`` itself; kept in sync each time a model is added):

* :mod:`database.models.currency` — ``Currency`` (R5 — ISO 4217 currency reference).
* :mod:`database.models.product_category` — ``ProductCategory`` (R1 — hierarchical product taxonomy).
* :mod:`database.models.product` — ``Product`` (M1 — core product master data).
* :mod:`database.models.product_lot` — ``ProductLot`` (M2 — batch/lot records for traceability).
* :mod:`database.models.role` — ``Role`` (R6 — RBAC role definitions).
* :mod:`database.models.permission` — ``Permission`` (R7 — RBAC permission definitions).
* :mod:`database.models.role_permission` — ``RolePermission`` (R8 — RBAC role-permission junction).
* :mod:`database.models.warehouse` — ``Warehouse`` (M4 — factory + representative warehouses).
* :mod:`database.models.app_user` — ``AppUser`` (M10 — system auth account linked to staff/rep).
* :mod:`database.models.representative` — ``Representative`` (M6 — sales representative master).
* :mod:`database.models.customer` — ``Customer`` (M8 — customer master, its own Aggregate Root).
* :mod:`database.models.bot_platform_ref` — ``BotPlatformRef`` (R12 — runtime-extensible bot platform catalog).
* :mod:`database.models.carrier` — ``Carrier`` (R14 — shipping carrier reference).
* :mod:`database.models.city_ref` — ``CityRef`` (R13 — canonical city/locality reference).
* :mod:`database.models.commission_config` — ``CommissionConfig`` (C1 — commission rate configuration).
* :mod:`database.models.discount` — ``Discount`` (H3 — defined discounts, scoped + time-bounded).
* :mod:`database.models.movement_type_ref` — ``MovementTypeRef`` (R4 — runtime-editable movement-type catalog).
* :mod:`database.models.notification_type_ref` — ``NotificationTypeRef`` (R9 — notification template/type catalog).
* :mod:`database.models.reason_code_ref` — ``ReasonCodeRef`` (R11 — standardized reason catalog).
* :mod:`database.models.report_type_ref` — ``ReportTypeRef`` (R10 — report-kind registry).
* :mod:`database.models.unit_of_measure` — ``UnitOfMeasure`` (R2 — UoM definitions and conversions).
* :mod:`database.models.uom_conversion` — ``UomConversion`` (R3 — conversion factors between UoMs).
* :mod:`database.models.inventory_transaction` — ``InventoryTransaction`` (T1 — append-only inventory ledger).
* :mod:`database.models.stock_reservation` — ``StockReservation`` (T2 — holds stock against a pending order).
* :mod:`database.models.inventory_balance_snapshot` — ``InventoryBalanceSnapshot`` (T3 — non-authoritative derived stock cache).
* :mod:`database.models.order` — ``Order`` (T10 — sales order header).
* :mod:`database.models.order_line` — ``OrderLine`` (T11 — order lines, frozen resolved price/discount).

This package intentionally re-exports nothing by name yet (no eager model
loading here) to keep ``import database`` cheap and to avoid forcing a model
import into the top-level package's public surface. Import models explicitly:

    >>> from database.models.currency import Currency

Authority:
    - database/base.py     — ``Base``, ``GuidPk``, ``id_column``.
    - database/mixins.py   — ``UniversalAuditColumns`` (UAC) / ``AppendOnlyAuditColumns``.
    - database/naming.py   — ``NAMING_CONVENTION`` (constraint/index naming).
    - docs/06_ERD.md        — entity definitions (PART B reference tables, etc.).
"""

from __future__ import annotations

__all__: list[str] = []
