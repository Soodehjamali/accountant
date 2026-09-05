from sqlalchemy.orm import configure_mappers

from database.base import Base
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
from database.models.bot_config import BotConfig
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

configure_mappers()

print("OK — mapper configuration succeeded, zero collisions")

names = (
    [
        c.name
        for t in Base.metadata.tables.values()
        for c in t.constraints
        if c.name
    ]
    +
    [
        i.name
        for t in Base.metadata.tables.values()
        for i in t.indexes
        if i.name
    ]
)

print("Found names:", len(names))

assert len(names) == len(set(names)), "collision found"
assert all(len(n) <= 63 for n in names), "name over 63 chars"

print("SUCCESS — no collisions and all names <= 63 chars")

# Spot-check the FK columns retrofitted from deferred UUID -> real
# ForeignKey() in this change: created_by/updated_by (UAC), created_by
# (AAC), warehouse.responsible_user_id, inventory_transaction.lot_id,
# inventory_transaction.actor_user_id.
checks = [
    ("order", "created_by", "app_user"),
    ("order", "updated_by", "app_user"),
    ("inventory_transaction", "created_by", "app_user"),
    ("warehouse", "responsible_user_id", "app_user"),
    ("inventory_transaction", "lot_id", "product_lot"),
    ("inventory_transaction", "actor_user_id", "app_user"),
    ("stock_reservation", "warehouse_id", "warehouse"),
    ("stock_reservation", "product_id", "product"),
    ("stock_reservation", "lot_id", "product_lot"),
    ("stock_reservation", "order_id", "order"),
    ("stock_reservation", "reserved_by", "app_user"),
    ("inventory_balance_snapshot", "warehouse_id", "warehouse"),
    ("inventory_balance_snapshot", "product_id", "product"),
    ("inventory_balance_snapshot", "lot_id", "product_lot"),
    ("order_line", "discount_id", "discount"),
    ("discount", "product_id", "product"),
    ("discount", "category_id", "product_category"),
    ("discount", "customer_id", "customer"),
    ("discount", "representative_id", "representative"),
    ("price_list", "currency_id", "currency"),
    ("order_line", "price_history_id", "price_history"),
    ("price_history", "product_id", "product"),
    ("price_history", "price_list_id", "price_list"),
    ("price_history", "currency_id", "currency"),
    ("order_status_history", "order_id", "order"),
    ("order_status_history", "actor_user_id", "app_user"),
    ("warehouse_location", "warehouse_id", "warehouse"),
    ("representative_contact", "representative_id", "representative"),
    ("customer_contact", "customer_id", "customer"),
    ("user_role", "user_id", "app_user"),
    ("user_role", "role_id", "role"),
    ("user_role", "assigned_by", "app_user"),
    ("warehouse_assignment", "representative_id", "representative"),
    ("warehouse_assignment", "warehouse_id", "warehouse"),
    ("customer_rep_assignment", "customer_id", "customer"),
    ("customer_rep_assignment", "representative_id", "representative"),
    ("costing_method_config", "locked_by", "app_user"),
    ("product_serial", "product_id", "product"),
    ("product_serial", "lot_id", "product_lot"),
    ("attachment", "uploaded_by", "app_user"),
    ("product_image", "product_id", "product"),
    ("product_image", "attachment_id", "attachment"),
    ("report_definition", "report_type_id", "report_type_ref"),
    ("report_definition", "owner_user_id", "app_user"),
    ("generated_document", "generated_by", "app_user"),
    ("purchase_price_history", "product_id", "product"),
    ("purchase_price_history", "lot_id", "product_lot"),
    ("purchase_price_history", "receiving_transaction_id", "inventory_transaction"),
    ("purchase_price_history", "currency_id", "currency"),
    # NOTE: generated_document.entity_id is intentionally NOT listed here --
    # like attachment.entity_id, it is a polymorphic reference with no
    # ForeignKey() by design (see database/models/generated_document.py's
    # module docstring). Only generated_document.generated_by is an
    # ordinary, single-target FK (and it is nullable).
    # NOTE: attachment.entity_id is intentionally NOT listed here -- it is a
    # polymorphic reference with no ForeignKey() by design (see
    # database/models/attachment.py's module docstring). Only
    # attachment.uploaded_by is an ordinary, single-target FK.
]
for table_name, column_name, referred_table in checks:
    table = Base.metadata.tables[f"erp.{table_name}"]
    col = table.columns[column_name]
    fks = list(col.foreign_keys)
    assert fks, f"{table_name}.{column_name} has no ForeignKey"
    referred = fks[0].column.table.name
    assert referred == referred_table, (
        f"{table_name}.{column_name} -> {referred}, expected {referred_table}"
    )
    print(f"OK — {table_name}.{column_name} -> {referred_table}.id (real FK, name={fks[0].constraint.name})")

print("\nSUCCESS — all retrofitted FKs verified")
