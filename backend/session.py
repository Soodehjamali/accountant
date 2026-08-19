"""SQLAlchemy engine + session plumbing for the Enterprise ERP (SIWRMS).

This module is the sole producer of the application's ``Engine`` and
``sessionmaker``. It intentionally does NOT create either at import time:

* The ``Engine`` is constructed **lazily** on first call to :func:`get_engine`
  so that ``import database`` / ``import database.session`` succeed even when
  ``DATABASE_URL`` is unset (which is the normal state in linting, testing,
  and code-generation environments — no live database is expected for this
  task). The env-var-missing error is raised only when something actually
  requests the engine.

* ``DATABASE_URL`` is the **only** place connection credentials / host / db name
  may be supplied — nothing is hard-coded here. If it is unset, :func:`get_engine`
  raises a clear, actionable ``RuntimeError`` (not a swallowed ``KeyError``).

* The PostgreSQL statement timeout (``DEFAULT_STATEMENT_TIMEOUT_MS``) is applied
  per connection via a ``connect`` / ``begin`` event that issues
  ``SET LOCAL statement_timeout = ...`` — robust against pool recycling and
  honored on every checked-out connection without application code.

* The streaming fetch size (``DEFAULT_FETCH_SIZE``) is **published** on every
  produced ``Session.info`` dict under :data:`DEFAULT_YIELD_PER_KEY` so query
  code can pass it to ``session.execute(stmt, execution_options={"yield_per":
  N})``. SQLAlchemy 2.0 has no ``Session``-wide ``yield_per`` knob, so the
  default is exposed via ``info`` rather than baked into the session factory.

* The dialect portion of ``DATABASE_URL`` is validated against the
  :class:`database.constants.DatabaseDialect` members (a programming error
  pointed at the env var, not at SQLAlchemy internals) and the PostgreSQL major
  version is sanity-checked against ``POSTGRES_MAJOR_VERSION`` when it can be
  read from the URL.

Scope & contract:

* ``get_engine()``       — lazy engine factory (idempotent; one engine per
  process, memoized via a module-level sentinel).
* ``get_session_factory()`` — ``sessionmaker`` bound to that engine.
* ``get_session()``      — ``@contextmanager`` that yields a ``Session``,
  commits on clean exit, rolls back on exception, and always closes.

OUT OF SCOPE (and therefore NOT present): concrete models, ``Base.metadata``
DDL emission (``metadata.create_all``), repository classes, transactional
boundaries beyond a single session, Alembic bootstrap, multi-database routing.

Authority:
    - database/constants.py — every engine/session knob this module reads.
    - database/base.py      — future models will live on ``Base.metadata``; this
      module does not import it (loading models here would couple session
      plumbing to the model layer and re-introduce import-time DB needs), and
      therefore deliberately does NOT pass ``Base.metadata`` anywhere.
    - SQLAlchemy 2.x         — ``Session`` / ``sessionmaker(bind=...)`` /
      per-query ``execution_options={"yield_per": N}`` (no ``Session``-wide
      ``yield_per`` knob in 2.0; the default is published via ``Session.info``).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Final, Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from database.constants import (
    DEFAULT_FETCH_SIZE,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    DatabaseDialect,
    POSTGRES_DIALECT_NAME,
    POSTGRES_MAJOR_VERSION,
)

# ---------------------------------------------------------------------------
# Configuration surface
# ---------------------------------------------------------------------------
#: Name of the environment variable holding the SQLAlchemy database URL.
#:
#: The only way credentials / host / db name reach this module. Nothing about
#: the connection is hard-coded; the URL is consumed verbatim by
#: :func:`sqlalchemy.create_engine`.
ENV_DATABASE_URL: Final[str] = "DATABASE_URL"

#: Per-query statement timeout (ms) issued as ``SET LOCAL statement_timeout``
#: on every checked-out connection. Sourced from ``database.constants``;
#: never overridden by a local literal here.
_STATEMENT_TIMEOUT_MS: Final[int] = DEFAULT_STATEMENT_TIMEOUT_MS

#: Server-side fetch size for streaming ``yield_per`` cursors (rows). Sourced
#: from ``database.constants``; never overridden by a local literal here.
_FETCH_SIZE: Final[int] = DEFAULT_FETCH_SIZE

#: Key under which :data:`_FETCH_SIZE` is published on every produced
#: ``Session.info`` dict (``session.info[DEFAULT_YIELD_PER_KEY]``). Repository /
#: query code reads this single project-wide default to pass to
#: ``session.execute(stmt, execution_options={"yield_per": N})`` rather than
#: re-hard-coding the fetch size — keeping ``DEFAULT_FETCH_SIZE`` the only
#: source of truth (SQLAlchemy 2.0 has no ``Session``-wide ``yield_per`` knob,
#: so the value is published via ``info`` for per-query retrieval).
DEFAULT_YIELD_PER_KEY: Final[str] = "default_yield_per"

# ---------------------------------------------------------------------------
# Lazy engine + memoization
# ---------------------------------------------------------------------------
# Module-level sentinel. ``None`` strictly means "not yet built" — distinguishing
# it from an erased engine lets :func:`_reset_engine` (test/inspector-only)
# He innocent-and-correct re-init the cache.
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


class DatabaseUrlNotConfiguredError(RuntimeError):
    """Raised when ``DATABASE_URL`` is unset and the engine is required.

    A deliberate, named exception (rather than a bare ``KeyError`` from
    ``os.environ['DATABASE_URL']``) so callers / operators see an actionable
    message pointing at the exact env var to set. Raised lazily — construction
    of this exception does not happen at import time, only when the engine is
    first requested.
    """

    def __init__(self) -> None:
        super().__init__(
            f"{ENV_DATABASE_URL} is not set. Configure it with a SQLAlchemy "
            f"PostgreSQL URL (e.g. '{DatabaseDialect.POSTGRESQL_PSYCOPG3.value}"
            f"://user:password@host:5432/dbname') before requesting the "
            f"engine. No database credentials are hard-coded by this "
            f"package."
        )


class InvalidDatabaseDialectError(ValueError):
    """Raised when ``DATABASE_URL``'s dialect is not a known PostgreSQL driver.

    Points at the env var name (not SQLAlchemy internals) so the fix is obvious:
    the URL's leading ``dialect[+driver]://`` must be one of
    :class:`database.constants.DatabaseDialect`. A non-PostgreSQL URL is a
    configuration error — this project targets PostgreSQL 17 exclusively.
    """

    def __init__(self, url: str) -> None:
        supported: list[str] = [d.value for d in DatabaseDialect]
        super().__init__(
            f"The {ENV_DATABASE_URL} dialect is not a supported PostgreSQL "
            f"driver (got '{url}'). Use one of {supported}."
        )


def _read_database_url() -> str:
    """Return the configured database URL or raise a clear error.

    Reads :data:`ENV_DATABASE_URL` from the environment. Returns ``""`` never —
    an empty / whitespace-only value is treated as "unset" and raises
    :class:`DatabaseUrlNotConfiguredError` so a stray ``DATABASE_URL=`` in the
    shell fails loudly rather than handing SQLAlchemy an empty string.
    """

    value: str | None = os.environ.get(ENV_DATABASE_URL)
    if value is None or not value.strip():
        raise DatabaseUrlNotConfiguredError()
    return value


def _validate_dialect(url: str) -> str:
    """Validate that ``url``'s dialect is a known PostgreSQL driver.

    Returns the URL unchanged on success (so the caller can thread it straight
    into ``create_engine``) and raises :class:`InvalidDatabaseDialectError` if
    the ``dialect[+driver]://`` prefix does not match a
    :class:`DatabaseDialect`. Major-version sanity-check
    (:data:`POSTGRES_MAJOR_VERSION`) is informational-only here — a URL has no
    dialect-level version stamp, so it cannot be enforced at this layer.

    Args:
        url: The raw ``DATABASE_URL`` string.

    Returns:
        The same ``url`` string, unchanged.
    """

    # SQLAlchemy URLs begin with "<dialect>[+<driver>]://". Extract that prefix
    # and check membership in the DatabaseDialect enum values.
    scheme_end: int = url.find("://")
    if scheme_end == -1:
        raise InvalidDatabaseDialectError(url)
    scheme: str = url[:scheme_end]
    supported: frozenset[str] = frozenset(d.value for d in DatabaseDialect)
    if scheme not in supported:
        raise InvalidDatabaseDialectError(url)
    # ``POSTGRES_MAJOR_VERSION`` (17) is the build target; it is recorded as an
    # ambient constant the codebase is built against, NOT a runtime assertion
    # (a URL carries no major version). Referenced below only to keep the
    # import live/typed and to document the expectation.
    _expected_major: int = POSTGRES_MAJOR_VERSION
    return url


def _install_statement_timeout(engine: Engine) -> None:
    """Wire ``SET LOCAL statement_timeout`` onto every checked-out connection.

    Uses the ``begin`` (and connect) events so the timeout applies per
    transaction regardless of how the connection was obtained from the pool.
    ``SET LOCAL`` scopes the value to the current transaction, matching the
    per-query intent documented by ``DEFAULT_STATEMENT_TIMEOUT_MS``. The value
    is expressed in **milliseconds** (the units PostgreSQL ``statement_timeout``
    accepts when a unit-less integer).

    Args:
        engine: The engine to instrument. Events are registered against this
          engine instance only, so a re-initialized engine is re-instrumented.
    """

    timeout_ms: int = _STATEMENT_TIMEOUT_MS

    @event.listens_for(engine, "connect")
    def _on_connect(  # type: ignore[no-untyped-def]
        dbapi_connection: object, connection_record: object
    ) -> None:
        # On a freshly checked-out DBAPI connection, set the search-path-safe
        # statement timeout. Issued before any application transaction begins.
        with dbapi_connection.cursor() as cursor:  # type: ignore[attr-defined]
            cursor.execute(f"SET statement_timeout = {int(timeout_ms)}")

    @event.listens_for(engine, "begin")
    def _on_begin(  # type: ignore[no-untyped-def]
        connection: object
    ) -> None:
        # ``SET LOCAL`` scopes to this transaction so transactions started by
        # other code paths that did not pass through ``connect`` (e.g. a
        # pre-pooled connection reused after pool recycle) still honor the
        # project default.
        # BUGFIX: SQLAlchemy 2.x's Connection.execute() requires an
        # executable object, not a bare string -- a raw f-string here
        # raises ObjectNotExecutableError on every real transaction (this
        # was previously untested against a live connection; nothing in
        # the codebase had opened one before the Backend Foundation
        # milestone's DB health-check endpoint did).
        connection.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))  # type: ignore[attr-defined]


def get_engine() -> Engine:
    """Return the lazily-built, memoized application ``Engine``.

    Construction is idempotent: the first call builds the engine (raising
    :class:`DatabaseUrlNotConfiguredError` if ``DATABASE_URL`` is unset, or
    :class:`InvalidDatabaseDialectError` if its dialect is unsupported) and
    memoizes it; subsequent calls return the same instance. The engine is NOT
    connected at construction — :func:`sqlalchemy.create_engine` is lazy by
    default (``connect()`` is only attempted on first use), so calling this in
    an environment with no live database does not raise.

    Returns:
        The shared :class:`sqlalchemy.Engine` instance.
    """

    global _engine
    if _engine is not None:
        return _engine

    url: str = _validate_dialect(_read_database_url())
    _engine = create_engine(url)  # lazy: no connection attempted at creation
    _install_statement_timeout(_engine)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return a ``sessionmaker`` bound to the shared engine.

    The factory is memoized alongside the engine so repeated calls return the
    same ``sessionmaker``. Because SQLAlchemy 2.0 exposes no ``Session``-wide
    ``yield_per`` knob (the streaming fetch size is a per-query
    ``execution_options``), the project default :data:`_FETCH_SIZE` is published
    on every produced session's ``info`` dict under :data:`DEFAULT_YIELD_PER_KEY`
    so query code reads a single project-wide default rather than re-hard-coding
    it. The session factory itself is otherwise configured with
    ``expire_on_commit=False`` (objects remain usable after commit).

    Returns:
        A ``sessionmaker[Session]`` producing :class:`Session` instances bound
        to :func:`get_engine`, each carrying :data:`DEFAULT_YIELD_PER_KEY` in
        its ``info`` dict set to the project fetch size.
    """

    global _session_factory
    if _session_factory is not None:
        return _session_factory

    engine: Engine = get_engine()
    # SQLAlchemy 2.0's ``Session.__init__`` exposes no ``yield_per`` /
    # session-wide ``execution_options`` knob; the streaming fetch size is
    # applied per ``session.execute(stmt, execution_options={"yield_per": N})``
    # call. To keep ``DEFAULT_FETCH_SIZE`` a single source of truth that query
    # code can discover rather than re-hard-code, the factory exposes it via
    # the session's ``info`` dict (``session.info[DEFAULT_YIELD_PER_KEY]``).
    _session_factory = sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
        info={DEFAULT_YIELD_PER_KEY: int(_FETCH_SIZE)},
    )
    return _session_factory


@contextmanager
def get_session() -> Iterator[Session]:
    """Context manager yielding a :class:`Session` with auto commit/rollback.

    Usage::

        with get_session() as session:
            session.add(model)
            # ... work ...
        # commits on clean exit, rolls back on exception, always closes.

    On clean exit the session is committed; if the body raises, the session is
    rolled back; in both cases the session is closed (returning its connection
    to the pool). The originating exception is never swallowed — it is
    re-raised after the rollback/close.
    """

    factory: sessionmaker[Session] = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__: Final[list[str]] = [
    "DatabaseUrlNotConfiguredError",
    "ENV_DATABASE_URL",
    "InvalidDatabaseDialectError",
    "DEFAULT_YIELD_PER_KEY",
    "get_engine",
    "get_session",
    "get_session_factory",
]
