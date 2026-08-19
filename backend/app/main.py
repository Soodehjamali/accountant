"""FastAPI application entry point (Backend Foundation milestone).

Run with:

    uvicorn app.main:app --reload --app-dir backend

Scope of this milestone (deliberately minimal -- see this task's own
constraints): app startup, GET /health, a DB connectivity check, API
versioning under /api/v1, and OpenAPI/Swagger (FastAPI's default
``/docs`` and ``/openapi.json``, enabled automatically -- no extra
configuration needed to satisfy requirement #10). No authentication, no
business/domain endpoints, no frontend.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import health as health_v1
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured :class:`FastAPI` instance.

    Factory pattern (rather than a bare module-level ``app``) so tests can
    construct an app with overridden settings if ever needed, without
    import-time side effects.
    """

    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        # FastAPI serves Swagger UI at /docs, ReDoc at /redoc, and the raw
        # schema at /openapi.json by default -- satisfies requirement #10
        # with no further configuration.
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Unversioned liveness probe at the app root (requirement #7) --
    # infra health checks (load balancers, container orchestrators)
    # conventionally hit a stable, unversioned path.
    app.include_router(health_v1.router, prefix="", tags=["health"])

    # Versioned API surface (requirement #9). Empty of business endpoints
    # in this milestone by design -- only health.router is registered on
    # api_router today.
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()

__all__ = ["app", "create_app"]
