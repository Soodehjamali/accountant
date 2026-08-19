"""Backend package for the Enterprise ERP (SIWRMS).

This package is intentionally separate from ``database/`` (the existing
SQLAlchemy foundation) and does not duplicate or modify anything in it.
The backend consumes ``database.session`` / ``database.base`` as-is.
"""
