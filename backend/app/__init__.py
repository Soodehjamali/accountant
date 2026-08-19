"""FastAPI application package (Backend Foundation milestone).

Structure:
    core/           -- app-level configuration (Settings).
    dependencies/   -- FastAPI Depends() providers (e.g. DB session).
    api/v1/         -- versioned HTTP routes.
    schemas/        -- Pydantic v2 request/response models.
    services/       -- business logic (empty placeholder in this milestone).
    repositories/   -- data-access logic (empty placeholder in this milestone).

This milestone deliberately implements ONLY: app startup, GET /health,
a DB connectivity check, API versioning, and OpenAPI/Swagger. No
authentication, no business/domain endpoints, no frontend.
"""
