"""FastAPI Depends() providers.

Keeps api/ endpoints decoupled from *how* a DB session or setting is
obtained -- endpoints declare ``Depends(get_db)`` / ``Depends(get_settings)``
and never import database.session directly.
"""
