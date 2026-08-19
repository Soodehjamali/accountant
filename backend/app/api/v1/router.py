"""Aggregates all API v1 endpoint routers under a single ``APIRouter``.

``main.py`` mounts this single router at ``settings.api_v1_prefix``
(``/api/v1``) -- individual endpoint modules never know their own final
mount path, keeping API versioning a one-place concern.

History: an earlier version of this file briefly contained two separate
``api_router = APIRouter()`` blocks (the second silently discarding the
first's ``include_router`` calls for ``health``/``auth``/``products``/
``inventory``/``rbac``) -- fixed in a prior pass; noted here so a future
edit merges into the single block below instead of repeating that bug.

``orders`` is still deliberately NOT included -- see
``backend/app/api/v1/endpoints/orders.py``'s own status note: it depends
on an Order state-machine design that is not yet written down anywhere
in the project's docs, and ``CLAUDE.md`` requires design approval before
that code is wired in.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import audit_log, auth, customers, health, inventory, products, rbac

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(products.router)
api_router.include_router(inventory.router)
api_router.include_router(rbac.router)
api_router.include_router(customers.router)
api_router.include_router(audit_log.router)

__all__ = ["api_router"]
