"""Aggregates all API v1 endpoint routers under a single ``APIRouter``.

``main.py`` mounts this single router at ``settings.api_v1_prefix``
(``/api/v1``) -- individual endpoint modules never know their own final
mount path, keeping API versioning a one-place concern.

History: an earlier version of this file briefly contained two separate
``api_router = APIRouter()`` blocks (the second silently discarding the
first's ``include_router`` calls for ``health``/``auth``/``products``/
``inventory``/``rbac``) -- fixed in a prior pass; noted here so a future
edit merges into the single block below instead of repeating that bug.

``orders`` is now included: ADR-004 (see ``09_Decisions.md``) accepted the
Order state-transition graph that was the blocker noted here previously,
and ``backend/app/api/v1/endpoints/orders.py`` has been rebuilt against
``services/order_service.py`` to implement it -- the same
service-wrapping pattern every other domain router below already uses.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import audit_log, auth, customers, health, inventory, orders, products, rbac

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(products.router)
api_router.include_router(inventory.router)
api_router.include_router(rbac.router)
api_router.include_router(customers.router)
api_router.include_router(audit_log.router)
api_router.include_router(orders.router)

__all__ = ["api_router"]
