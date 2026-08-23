"""Bootstrap/seed helpers -- get-or-create the minimal rows every other
service depends on via NOT NULL foreign keys.

``product.created_by`` (via ``UniversalAuditColumns``) is a NOT NULL FK to
``app_user.id``, and ``product.base_uom_id`` is a NOT NULL FK to
``unit_of_measure.id``. Neither table has any rows in a freshly migrated
database, so before the very first product can be created, something has to
exist for both. This module is that "something" -- idempotent, get-or-create
helpers, safe to call every time the app/script starts.

Nothing here is a real auth/onboarding flow (no password policy, no UoM
catalog management UI). It exists purely to unblock Task 3/4 development
against a real database. A future Task 3/4 milestone replaces
``ensure_system_user`` with real user registration/login and
``ensure_default_uom`` with a proper UoM catalog admin screen.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.app_user import AppUser
from database.models.currency import Currency
from database.models.movement_type_ref import MovementTypeRef
from database.models.permission import Permission
from database.models.role import Role
from database.models.role_permission import RolePermission
from database.models.unit_of_measure import UnitOfMeasure
from database.models.user_role import UserRole
from database.models.warehouse import Warehouse

#: Fixed username/UoM code for the seeded rows so re-running the bootstrap
#: is idempotent (get-or-create keyed on these constants) rather than
#: creating a new row every run.
SYSTEM_USERNAME = "system"
SYSTEM_EMAIL = "system@local.invalid"
DEFAULT_UOM_CODE = "PCS"
DEFAULT_CURRENCY_CODE = "IRR"
DEFAULT_WAREHOUSE_CODE = "MAIN"

#: RBAC bootstrap constants -- the role/permission that break the RBAC
#: chicken-and-egg problem (something has to be able to grant the very
#: first permission on a fresh database). See ``ensure_rbac_bootstrap``.
ADMIN_ROLE_CODE = "ADMIN"
RBAC_MANAGE_PERMISSION_CODE = "RBAC_MANAGE"

#: code -> (sign, label) for the PART A ``MovementType`` value set (see
#: ``database/models/movement_type_ref.py``'s own docstring for the
#: authoritative list of 12 tokens). Signs for RECEIPT_FROM_PRODUCTION,
#: TRANSFER_IN/OUT, SALE_OUT, SALE_RETURN_IN, ADJUSTMENT_POSITIVE/NEGATIVE
#: and INITIAL_OPENING_BALANCE follow directly from their names/ERD
#: semantics. FACTORY_DIRECT_SHIPMENT, DAMAGED_OUT,
#: CONSIGNMENT_SELLTHROUGH_OWNERSHIP, and REVERSAL are this reconstruction's
#: own reasonable-default choice (not derived from any recovered document)
#: -- please review against your intended business rules, especially
#: REVERSAL, whose sign is deliberately never checked by
#: ``services.inventory_service.reverse_transaction`` (see that module).
_MOVEMENT_TYPES: dict[str, tuple[int, str]] = {
    "RECEIPT_FROM_PRODUCTION": (1, "Receipt from production"),
    "TRANSFER_IN": (1, "Transfer in"),
    "TRANSFER_OUT": (-1, "Transfer out"),
    "SALE_OUT": (-1, "Sale"),
    "SALE_RETURN_IN": (1, "Sale return"),
    "ADJUSTMENT_POSITIVE": (1, "Positive adjustment"),
    "ADJUSTMENT_NEGATIVE": (-1, "Negative adjustment"),
    "DAMAGED_OUT": (-1, "Damaged / written off"),
    "FACTORY_DIRECT_SHIPMENT": (-1, "Factory-direct shipment"),
    "INITIAL_OPENING_BALANCE": (1, "Initial opening balance"),
    "REVERSAL": (1, "System-generated reversal"),
    "CONSIGNMENT_SELLTHROUGH_OWNERSHIP": (1, "Consignment sell-through ownership transfer"),
}


def ensure_system_user(session: Session) -> AppUser:
    """Return the seeded "system" ``AppUser``, creating it if absent.

    ``app_user.created_by`` is itself a NOT NULL, self-referencing FK to
    ``app_user.id`` (every ``AppUser`` row was "created by" some user,
    including the very first one). This is resolved by pre-generating the
    row's UUID client-side and pointing ``created_by`` at that same UUID --
    valid because PostgreSQL checks FK constraints via an AFTER-row trigger,
    so a single-row self-referencing INSERT is legal (see
    ``database/mixins.py``'s own docstring note on this).
    """

    existing = session.execute(
        select(AppUser).where(AppUser.username == SYSTEM_USERNAME)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    new_id = uuid.uuid4()
    user = AppUser(
        id=new_id,
        username=SYSTEM_USERNAME,
        email=SYSTEM_EMAIL,
        # Placeholder only -- this bootstrap user is not a real login
        # account; a real password/auth flow is Task 3/4 scope.
        password_hash="not-a-real-hash",
        status="ACTIVE",
        created_by=new_id,
    )
    session.add(user)
    session.flush()  # populate any server-side defaults before returning
    return user


def ensure_default_uom(session: Session, actor_id: uuid.UUID) -> UnitOfMeasure:
    """Return the seeded "PCS" (pieces) ``UnitOfMeasure``, creating it if absent."""

    existing = session.execute(
        select(UnitOfMeasure).where(UnitOfMeasure.code == DEFAULT_UOM_CODE)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    uom = UnitOfMeasure(
        code=DEFAULT_UOM_CODE,
        name="Pieces",
        class_="BASE",
        created_by=actor_id,
    )
    session.add(uom)
    session.flush()
    return uom


def ensure_default_currency(session: Session, actor_id: uuid.UUID) -> Currency:
    """Return the seeded base ``Currency`` (IRR), creating it if absent."""

    existing = session.execute(
        select(Currency).where(Currency.code == DEFAULT_CURRENCY_CODE)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    currency = Currency(
        code=DEFAULT_CURRENCY_CODE,
        symbol="ريال",
        decimals=0,
        is_base=True,
        created_by=actor_id,
    )
    session.add(currency)
    session.flush()
    return currency


def ensure_default_warehouse(session: Session, actor_id: uuid.UUID) -> Warehouse:
    """Return the seeded default ``Warehouse`` (MAIN), creating it if absent."""

    existing = session.execute(
        select(Warehouse).where(Warehouse.code == DEFAULT_WAREHOUSE_CODE)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    warehouse = Warehouse(
        code=DEFAULT_WAREHOUSE_CODE,
        name="Main Warehouse",
        type="FACTORY",
        ownership_mode="OWNED",
        status="ACTIVE",
        created_by=actor_id,
    )
    session.add(warehouse)
    session.flush()
    return warehouse


def ensure_movement_types(session: Session, actor_id: uuid.UUID) -> list[MovementTypeRef]:
    """Return the seeded ``movement_type_ref`` catalog (all 12 PART A tokens),
    creating any missing rows. Idempotent -- safe to call every run."""

    result: list[MovementTypeRef] = []
    for code, (sign, label) in _MOVEMENT_TYPES.items():
        existing = session.execute(
            select(MovementTypeRef).where(MovementTypeRef.code == code)
        ).scalar_one_or_none()
        if existing is not None:
            result.append(existing)
            continue
        movement_type = MovementTypeRef(code=code, sign=sign, label=label, created_by=actor_id)
        session.add(movement_type)
        session.flush()
        result.append(movement_type)
    return result


#: (code, name, resource, action) for every permission that should be
#: granted to the ADMIN role by ``ensure_rbac_bootstrap``, beyond
#: RBAC_MANAGE itself (which gets the chicken-and-egg treatment below
#: because it's what lets ADMIN grant anything at all). Each entry here
#: corresponds to a permission a real endpoint already checks via
#: ``require_permission(...)`` -- CUSTOMER_MANAGE
#: (``endpoints/customers.py``) and AUDIT_LOG_VIEW
#: (``endpoints/audit_log.py``) -- so that ADMIN can actually use those
#: endpoints out of the box instead of the operator having to
#: rediscover and manually grant every new permission code a future
#: endpoint introduces. Extend this tuple, not ``ensure_rbac_bootstrap``
#: itself, when a future endpoint adds another ``require_permission(...)``
#: gate that ADMIN should hold by default.
_ADMIN_DEFAULT_PERMISSIONS: tuple[tuple[str, str, str, str], ...] = (
    ("CUSTOMER_MANAGE", "Manage customers", "customer", "manage"),
    ("AUDIT_LOG_VIEW", "View audit log", "audit_log", "view"),
    ("ORDER_MANAGE", "Manage sales orders", "order", "manage"),
    ("ORDER_APPROVE", "Approve sales orders", "order", "approve"),
    ("INVOICE_MANAGE", "Manage invoices", "invoice", "manage"),
    ("TRANSFER_MANAGE", "Manage stock transfers", "stock_transfer", "manage"),
    ("PAYMENT_MANAGE", "Manage payments", "payment", "manage"),
)


def _ensure_permission(
    session: Session, *, code: str, name: str, resource: str, action: str, actor_id: uuid.UUID
) -> Permission:
    """Get-or-create a single ``Permission`` row. Idempotent."""

    permission = session.execute(
        select(Permission).where(Permission.code == code)
    ).scalar_one_or_none()
    if permission is None:
        permission = Permission(
            code=code, name=name, resource=resource, action=action, created_by=actor_id
        )
        session.add(permission)
        session.flush()
    return permission


def _ensure_grant(
    session: Session, *, role: Role, permission: Permission, actor_id: uuid.UUID
) -> None:
    """Get-or-create a single ``RolePermission`` grant row. Idempotent."""

    grant = session.execute(
        select(RolePermission).where(
            RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id,
        )
    ).scalar_one_or_none()
    if grant is None:
        session.add(
            RolePermission(role_id=role.id, permission_id=permission.id, created_by=actor_id)
        )
        session.flush()


def ensure_rbac_bootstrap(session: Session) -> None:
    """Seed the ``ADMIN`` role holding ``RBAC_MANAGE`` (and every permission
    listed in ``_ADMIN_DEFAULT_PERMISSIONS``), and grant that role to the
    seeded system user -- breaks the RBAC chicken-and-egg problem (something
    has to be able to grant the very first permission on a fresh database).
    Idempotent -- safe to call every run.
    """

    system_user = ensure_system_user(session)

    role = session.execute(
        select(Role).where(Role.code == ADMIN_ROLE_CODE)
    ).scalar_one_or_none()
    if role is None:
        role = Role(
            code=ADMIN_ROLE_CODE,
            name="Administrator",
            description="Full RBAC administration access.",
            created_by=system_user.id,
        )
        session.add(role)
        session.flush()

    rbac_manage = _ensure_permission(
        session,
        code=RBAC_MANAGE_PERMISSION_CODE,
        name="Manage roles and permissions",
        resource="rbac",
        action="manage",
        actor_id=system_user.id,
    )
    _ensure_grant(session, role=role, permission=rbac_manage, actor_id=system_user.id)

    for code, name, resource, action in _ADMIN_DEFAULT_PERMISSIONS:
        permission = _ensure_permission(
            session, code=code, name=name, resource=resource, action=action,
            actor_id=system_user.id,
        )
        _ensure_grant(session, role=role, permission=permission, actor_id=system_user.id)

    assignment = session.execute(
        select(UserRole).where(
            UserRole.user_id == system_user.id, UserRole.role_id == role.id
        )
    ).scalar_one_or_none()
    if assignment is None:
        session.add(
            UserRole(
                user_id=system_user.id,
                role_id=role.id,
                assigned_by=system_user.id,
                created_by=system_user.id,
            )
        )
        session.flush()


__all__ = [
    "ADMIN_ROLE_CODE",
    "DEFAULT_CURRENCY_CODE",
    "DEFAULT_UOM_CODE",
    "DEFAULT_WAREHOUSE_CODE",
    "RBAC_MANAGE_PERMISSION_CODE",
    "SYSTEM_USERNAME",
    "ensure_default_currency",
    "ensure_default_uom",
    "ensure_default_warehouse",
    "ensure_movement_types",
    "ensure_rbac_bootstrap",
    "ensure_system_user",
]
