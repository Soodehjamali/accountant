"""FastAPI dependency providing a request-scoped SQLAlchemy Session.

Wraps ``database.session.get_session_factory()`` (the existing, unmodified
DB layer) rather than reimplementing engine/session construction. This
milestone has no write endpoints, so no explicit commit is issued here;
the session is always closed, and rolled back if the request raised.
"""

from __future__ import annotations

from typing import Iterator

from sqlalchemy.orm import Session

from database.session import get_session_factory


def get_db() -> Iterator[Session]:
    """Yield a SQLAlchemy ``Session`` for the duration of one request.

    Usage in an endpoint::

        def handler(db: Session = Depends(get_db)) -> ...:
            ...
    """

    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["get_db"]
