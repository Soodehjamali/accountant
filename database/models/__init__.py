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
* :mod:`database.models.product_serial` — ``ProductSerial`` (M3 — unit-level serial tracking, optional per product).
* :mod:`database.models.role` — ``Role`` (R6 — RBAC role definitions).
* :mod:`database.models.permission` — ``Permission`` (R7 — RBAC permission definitions).
* :mod:`database.models.role_permission` — ``RolePermission`` (R8 — RBAC role-permission junction).
* :mod:`database.models.warehouse` — ``Warehouse`` (M4 — factory + representative warehouses).
* :mod:`database.models.warehouse_location` — ``WarehouseLocation`` (M5 — optional bins/sub-locations within a warehouse).
* :mod:`database.models.app_user` — ``AppUser`` (M10 — system auth account linked to staff/rep).
* :mod:`database.models.user_role` — ``UserRole`` (M11 — RBAC user-role junction).
* :mod:`database.models.representative` — ``Representative`` (M6 — sales representative master).
* :mod:`database.models.representative_contact` — ``RepresentativeContact`` (M7 — rep contact relationships: phone, email).
* :mod:`database.models.customer` — ``Customer`` (M8 — customer master, its own Aggregate Root).
* :mod:`database.models.customer_contact` — ``CustomerContact`` (M9 — multiple contacts per customer).
* :mod:`database.models.customer_rep_assignment` — ``CustomerRepAssignment`` (C6 — customer ↔ representative assignment, with effective date and reassignment history; also acts as history).
* :mod:`database.models.bot_platform_ref` — ``BotPlatformRef`` (R12 — runtime-extensible bot platform catalog).
* :mod:`database.models.carrier` — ``Carrier`` (R14 — shipping carrier reference).
* :mod:`database.models.city_ref` — ``CityRef`` (R13 — canonical city/locality reference).
* :mod:`database.models.commission_config` — ``CommissionConfig`` (C1 — commission rate configuration).
* :mod:`database.models.costing_method_config` — ``CostingMethodConfig`` (C2 — org-level costing method (FIFO/LIFO/WEIGHTED_AVERAGE), single-row, locked after financial transactions exist).
* :mod:`database.models.system_config` — ``SystemConfig`` (C4 — key/value runtime tunables, key independently unique).
* :mod:`database.models.customer_ledger` — ``CustomerLedger`` (M13 — non-authoritative per-customer running-balance cache).
* :mod:`database.models.bot_binding_token` — ``BotBindingToken`` (persistent binding token for bot identity binding, single-use, short-lived).
* :mod:`database.models.bot_session` — ``BotSession`` (M12 — bind a messenger-platform user to a representative identity).
* :mod:`database.models.price_list` — ``PriceList`` (C3 — named price list binding price versions to a scope).
* :mod:`database.models.warehouse_assignment` — ``WarehouseAssignment`` (C5 — representative ↔ warehouse assignment with primary flag).
* :mod:`database.models.price_history` — ``PriceHistory`` (H1 — immutable versioned selling price, append-only).
* :mod:`database.models.purchase_price_history` — ``PurchasePriceHistory`` (H2 — immutable input-cost record per receiving transaction, append-only).
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
* :mod:`database.models.order_status_history` — ``OrderStatusHistory`` (T12/H5 — immutable order state-machine log).
* :mod:`database.models.order_price_freeze` — ``OrderPriceFreeze`` (T13/H6 — price-resolution precedence audit record, append-only; optional per ERD, built standalone per the spec's own resolved decision).
* :mod:`database.models.stock_transfer` — ``StockTransfer`` (T4 — transfer header between warehouses).
* :mod:`database.models.transfer_line` — ``TransferLine`` (T5 — line items of a stock transfer).
* :mod:`database.models.transfer_history` — ``TransferHistory`` (T6/H4 — immutable transfer state-change log, append-only).
* :mod:`database.models.stock_adjustment` — ``StockAdjustment`` (T7 — manual correction / damage / write-off request, posts to inventory_transaction on APPLIED).
* :mod:`database.models.physical_count` — ``PhysicalCount`` (T8 — stocktake session header).
* :mod:`database.models.physical_count_line` — ``PhysicalCountLine`` (T9 — counted vs. expected quantity per product/lot, with computed delta).
* :mod:`database.models.attachment` — ``Attachment`` (M14 — generic polymorphic file attachment; entity_id has no ForeignKey by design).
* :mod:`database.models.generated_document` — ``GeneratedDocument`` (M16 — system-generated PDF/document storage, append-only per version; entity_id has no ForeignKey by design; entity_type IS CHECK-bounded, unlike attachment.entity_type).
* :mod:`database.models.product_image` — ``ProductImage`` (M15 — product-specific images; specializes attachment with display concerns).
* :mod:`database.models.report_definition` — ``ReportDefinition`` (M17 — saved/scheduled report configuration; first JSONB column in this codebase).
* :mod:`database.models.shipment` — ``Shipment`` (T14 — physical shipment tied to an order).
* :mod:`database.models.shipment_line` — ``ShipmentLine`` (T15 — lines in a shipment matching order lines).
* :mod:`database.models.shipment_status_history` — ``ShipmentStatusHistory`` (T16/H7 — immutable shipment state log, incl. geo-tracking, append-only).
* :mod:`database.models.invoice` — ``Invoice`` (T17 — invoice header generated from shipped/fulfilled orders).
* :mod:`database.models.invoice_line` — ``InvoiceLine`` (T18 — invoice line items, price frozen at issue time).
* :mod:`database.models.payment` — ``Payment`` (T19 — money received from a customer, append-only per spec).
* :mod:`database.models.payment_allocation` — ``PaymentAllocation`` (T20/J2 — payment-to-invoice allocation junction; no audit mixin, per spec's own bare column list — see module docstring).
* :mod:`database.models.notification` — ``Notification`` (T24 — outbound/internal notification record for a user or a representative).
* :mod:`database.models.approval_request` — ``ApprovalRequest`` (T25 — live approval task raised against any approvable entity, polymorphic; exactly one PENDING per entity enforced via partial unique index).
* :mod:`database.models.approval_history` — ``ApprovalHistory`` (H7 — immutable log of approval_request status transitions, append-only).
* :mod:`database.models.audit_log` — ``AuditLog`` (H6 — system-wide immutable audit trail covering every state-changing action, append-only).
* :mod:`database.models.kpi_snapshot` — ``KpiSnapshot`` (H10 — periodic immutable capture of headline KPIs for dashboards/trend charts, append-only).
* :mod:`database.models.credit_limit_config` — ``CreditLimitConfig`` (C7 — optional per-segment credit limits beyond those on customer; see the module's own docstring for a caveat on column-list provenance).
* :mod:`database.models.invoice_order` — ``InvoiceOrder`` (J1 — resolving N:N junction between invoice and order; no audit mixin, composite PK, no surrogate id).
* :mod:`database.models.credit_note` — ``CreditNote`` (T20 — formal correction instrument against a closed/issued invoice).
* :mod:`database.models.credit_note_line` — ``CreditNoteLine`` (T21 — line items of a credit_note, mirroring the invoice lines being corrected).
* :mod:`database.models.customer_ledger_entry` — ``CustomerLedgerEntry`` (T22 — immutable, append-only accounts-receivable ledger; the authoritative source of truth customer_ledger (M13) is a cache of).
* :mod:`database.models.commission_transaction` — ``CommissionTransaction`` (T23 — event-sourced commission ledger per representative: accrual/approval/payment/clawback, append-only).
* :mod:`database.models.customer_return` — ``CustomerReturn`` (T27 — header for a physical return event: customer return, rep return-to-factory, or damaged return).
* :mod:`database.models.return_line` — ``ReturnLine`` (T28 — line-level detail of a return: product, quantity, post-inspection condition/disposition; last table of the 78-table schema).

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

# Eagerly import every concrete ORM model module so that ``Base.metadata``
# has every table registered -- and every string-based ``ForeignKey("<table>.id")``
# target resolvable -- as soon as ``database.models`` (or any
# ``database.models.<submodule>``) is imported anywhere in the app.
#
# Why this is necessary: SQLAlchemy resolves string ``ForeignKey()`` targets
# lazily, against ``Base.metadata.tables``, the first time mappers are
# configured (e.g. on the first ``Session.flush()``/``.add()``). If a caller
# only imports the one or two model modules it directly needs (e.g.
# ``from database.models.product import Product``), any *other* model that
# ``product`` merely references by table name in a ``ForeignKey`` string
# (like ``product_category``) is never loaded, its table is never registered,
# and mapper configuration fails with ``NoReferencedTableError`` even though
# the code "worked" for callers that happened to import the referenced model
# first for unrelated reasons.
#
# Importing every model here means any of these three (equivalent) import
# spellings triggers full metadata registration, because Python always runs
# a package's ``__init__.py`` before any of its submodules:
#
#     import database.models
#     from database.models import product
#     from database.models.product import Product
#
# This mirrors ``database/models/check_mappers.py``'s own import list (a
# standalone diagnostic script asserting zero mapper/name collisions across
# the full 78-table schema) -- that list is the source of truth this was
# generated from, kept in the same order.
from database.models.app_user import AppUser
from database.models.approval_request import ApprovalRequest
from database.models.approval_history import ApprovalHistory
from database.models.attachment import Attachment
from database.models.audit_log import AuditLog
from database.models.bot_platform_ref import BotPlatformRef
from database.models.carrier import Carrier
from database.models.city_ref import CityRef
from database.models.commission_config import CommissionConfig
from database.models.costing_method_config import CostingMethodConfig
from database.models.currency import Currency
from database.models.customer import Customer
from database.models.customer_contact import CustomerContact
from database.models.customer_rep_assignment import CustomerRepAssignment
from database.models.bot_binding_token import BotBindingToken
from database.models.bot_session import BotSession
from database.models.bot_message_log import BotMessageLog
from database.models.customer_ledger import CustomerLedger
from database.models.customer_ledger_entry import CustomerLedgerEntry
from database.models.discount import Discount
from database.models.generated_document import GeneratedDocument
from database.models.inventory_balance_snapshot import InventoryBalanceSnapshot
from database.models.inventory_transaction import InventoryTransaction
from database.models.invoice import Invoice
from database.models.invoice_line import InvoiceLine
from database.models.invoice_history import InvoiceHistory
from database.models.movement_type_ref import MovementTypeRef
from database.models.notification_type_ref import NotificationTypeRef
from database.models.order import Order
from database.models.invoice_order import InvoiceOrder
from database.models.order_line import OrderLine
from database.models.order_price_freeze import OrderPriceFreeze
from database.models.order_status_history import OrderStatusHistory
from database.models.payment import Payment
from database.models.payment_allocation import PaymentAllocation
from database.models.permission import Permission
from database.models.physical_count import PhysicalCount
from database.models.physical_count_line import PhysicalCountLine
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.product_category import ProductCategory
from database.models.product_image import ProductImage
from database.models.product_lot import ProductLot
from database.models.product_serial import ProductSerial
from database.models.purchase_price_history import PurchasePriceHistory
from database.models.reason_code_ref import ReasonCodeRef
from database.models.credit_note import CreditNote
from database.models.credit_note_line import CreditNoteLine
from database.models.report_definition import ReportDefinition
from database.models.report_run import ReportRun
from database.models.report_type_ref import ReportTypeRef
from database.models.representative import Representative
from database.models.commission_transaction import CommissionTransaction
from database.models.credit_limit_config import CreditLimitConfig
from database.models.notification import Notification
from database.models.notification_history import NotificationHistory
from database.models.report_snapshot import ReportSnapshot
from database.models.representative_contact import RepresentativeContact
from database.models.role import Role
from database.models.role_permission import RolePermission
from database.models.shipment import Shipment
from database.models.shipment_line import ShipmentLine
from database.models.shipment_status_history import ShipmentStatusHistory
from database.models.stock_adjustment import StockAdjustment
from database.models.stock_reservation import StockReservation
from database.models.stock_transfer import StockTransfer
from database.models.system_config import SystemConfig
from database.models.transfer_history import TransferHistory
from database.models.transfer_line import TransferLine
from database.models.unit_of_measure import UnitOfMeasure
from database.models.uom_conversion import UomConversion
from database.models.user_role import UserRole
from database.models.warehouse import Warehouse
from database.models.customer_return import CustomerReturn
from database.models.return_line import ReturnLine
from database.models.kpi_snapshot import KpiSnapshot
from database.models.warehouse_assignment import WarehouseAssignment
from database.models.warehouse_location import WarehouseLocation

__all__: list[str] = []
