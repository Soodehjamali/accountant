from sqlalchemy.orm import configure_mappers

from database.base import Base
from database.models.currency import Currency
from database.models.product_category import ProductCategory
from database.models.product import Product
from database.models.product_lot import ProductLot
from database.models.role import Role
from database.models.permission import Permission
from database.models.role_permission import RolePermission
from database.models.warehouse import Warehouse
from database.models.app_user import AppUser
from database.models.representative import Representative
from database.models.bot_platform_ref import BotPlatformRef
from database.models.carrier import Carrier
from database.models.city_ref import CityRef
from database.models.commission_config import CommissionConfig
from database.models.movement_type_ref import MovementTypeRef
from database.models.notification_type_ref import NotificationTypeRef
from database.models.reason_code_ref import ReasonCodeRef
from database.models.report_type_ref import ReportTypeRef
from database.models.unit_of_measure import UnitOfMeasure
# بقیه مدل‌ها را هم اضافه می‌کنیم

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