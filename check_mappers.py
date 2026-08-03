from sqlalchemy.orm import configure_mappers

from database.base import Base
from database.models.app_user import AppUser
from database.models.bot_platform_ref import BotPlatformRef
from database.models.carrier import Carrier
from database.models.city_ref import CityRef
from database.models.commission_config import CommissionConfig
from database.models.currency import Currency
from database.models.customer import Customer
from database.models.discount import Discount
from database.models.inventory_balance_snapshot import InventoryBalanceSnapshot
from database.models.inventory_transaction import InventoryTransaction
from database.models.movement_type_ref import MovementTypeRef
from database.models.notification_type_ref import NotificationTypeRef
from database.models.order import Order
from database.models.order_line import OrderLine
from database.models.order_status_history import OrderStatusHistory
from database.models.permission import Permission
from database.models.price_history import PriceHistory
from database.models.price_list import PriceList
from database.models.product import Product
from database.models.product_category import ProductCategory
from database.models.product_lot import ProductLot
from database.models.reason_code_ref import ReasonCodeRef
from database.models.report_type_ref import ReportTypeRef
from database.models.representative import Representative
from database.models.role import Role
from database.models.role_permission import RolePermission
from database.models.stock_reservation import StockReservation
from database.models.unit_of_measure import UnitOfMeasure
from database.models.uom_conversion import UomConversion
from database.models.warehouse import Warehouse

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
