"""ORM models package for the Enterprise ERP (SIWRMS).

Concrete SQLAlchemy 2.x ORM models for the application. Each model lives in its
own module and declares a single ERD/entity table against the shared
:class:`database.base.Base` (which is bound to the project ``MetaData`` that
carries :data:`database.naming.NAMING_CONVENTION`).

Submodules:

* :mod:`database.models.currency` — ``R5 — currency`` (ISO 4217 reference).
* :mod:`database.models.product_category` — ``R1 — product_category`` (hierarchical product taxonomy).
* :mod:`database.models.product` — ``M1 — product`` (core product master data).
* :mod:`database.models.product_lot` — ``M2 — product_lot`` (batch/lot records for traceability).
* :mod:`database.models.role` — ``R6 — role`` (RBAC role definition).
* :mod:`database.models.permission` — ``R7 — permission`` (RBAC permission definition).
* :mod:`database.models.role_permission` — ``R8 — role_permission`` (RBAC role-permission junction).
* :mod:`database.models.warehouse` — ``M4 — warehouse`` (factory + representative warehouses).
* :mod:`database.models.app_user` — ``M10 — app_user`` (system auth account linked to staff/rep).
* :mod:`database.models.representative` — ``M6 — representative`` (sales representative master).
* :mod:`database.models.commission_config` — ``C1 — commission_config`` (commission rate configuration).
* :mod:`database.models.customer` — ``M8 — customer`` (independent customer Aggregate Root).

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
