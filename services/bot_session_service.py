"""Platform-agnostic bot session service (``bot_session`` M12 / ``bot_message_log`` H5).

Per ``services/__init__.py``'s documented convention, every function here
takes an already-open ``Session`` and never commits/closes it -- that is
the caller's job.  Mirrors ``services/rbac_service.py`` in shape.

This module is the **sole sanctioned write path** onto ``bot_session`` and
``bot_message_log``.  No adapter or command handler should construct
``BotSession`` or ``BotMessageLog`` rows directly.

Design:
    Session lifecycle:
        ``get_or_create_session()`` -- idempotent lookup-or-create by
        (platform_code, platform_user_id).  Called on every incoming
        message; returns an existing ``LINKED`` session or raises
        ``SessionNotLinkedError``.

        ``create_binding()`` -- admin-initiated: creates a new
        ``LINKED`` session from a validated binding token.  Only callable
        by users with ``BOT_MANAGE`` permission.

        ``revoke_session()`` -- sets ``status = REVOKED``.  Only callable
        by users with ``BOT_MANAGE`` permission.

    Message logging:
        ``log_inbound()`` / ``log_outbound()`` -- append-only inserts
        into ``bot_message_log``.

    Binding tokens:
        ``generate_binding_token()`` -- creates a short-lived, single-use
        token associating a ``Representative`` with a ``BotPlatformRef``.
        The raw token is returned once; only its SHA-256 hash is persisted
        in the ``bot_binding_token`` table.  Tokens expire after 30 minutes
        and are invalidated after consumption.

    Identity resolution:
        ``resolve_session()`` -- given (platform_code, platform_user_id),
        return the active ``LINKED`` session or ``None``.

Authorization:
    This module does NOT check permissions -- that is the caller's
    responsibility (the command service or the API endpoint).  This
    module only enforces data-integrity invariants (e.g. one platform
    user <-> one representative, status transitions).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.bot_binding_token import BotBindingToken
from database.models.bot_message_log import BotMessageLog
from database.models.bot_platform_ref import BotPlatformRef
from database.models.bot_session import BotSession
from database.models.representative import Representative


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SessionNotLinkedError(LookupError):
    """Raised when no active LINKED session exists for a platform identity."""

    def __init__(self, platform_code: str, platform_user_id: str) -> None:
        super().__init__(
            f"No linked bot session for platform '{platform_code}' "
            f"user '{platform_user_id}'."
        )
        self.platform_code = platform_code
        self.platform_user_id = platform_user_id


class InvalidBindingTokenError(ValueError):
    """Raised when a binding token is invalid, expired, or already used."""

    def __init__(self, token: str) -> None:
        super().__init__(f"Binding token '{token}' is invalid or expired.")
        self.token = token


class SessionAlreadyLinkedError(ValueError):
    """Raised when attempting to create a binding for an already-linked identity."""

    def __init__(self, platform_code: str, platform_user_id: str) -> None:
        super().__init__(
            f"Platform '{platform_code}' user '{platform_user_id}' is already linked."
        )
        self.platform_code = platform_code
        self.platform_user_id = platform_user_id


class PlatformNotFoundError(LookupError):
    """Raised when a platform_code has no matching ``bot_platform_ref`` row."""

    def __init__(self, platform_code: str) -> None:
        super().__init__(f"No bot_platform_ref with code '{platform_code}' exists.")
        self.platform_code = platform_code


class RepresentativeNotFoundError(LookupError):
    """Raised when a representative_id has no matching ``representative`` row."""

    def __init__(self, representative_id: uuid.UUID) -> None:
        super().__init__(f"No representative with id '{representative_id}' exists.")
        self.representative_id = representative_id


# ---------------------------------------------------------------------------
# Binding token constants
# ---------------------------------------------------------------------------

_BINDING_TOKEN_TTL = timedelta(minutes=30)


def _hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a raw binding token.

    Only the hash is persisted; the raw token is returned to the admin
    exactly once and never stored.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Binding token CRUD (persistent, single-use, short-lived)
# ---------------------------------------------------------------------------

def generate_binding_token(
    session: Session,
    *,
    representative_id: uuid.UUID,
    platform_code: str,
    created_by: uuid.UUID,
) -> str:
    """Generate a short-lived, single-use binding token.

    The token associates a ``Representative`` with a ``BotPlatformRef``.
    Only callable by administrators (the caller must hold ``BOT_MANAGE``).

    The raw token is returned to the caller exactly once.  Only its
    SHA-256 hash is persisted in ``bot_binding_token``.

    ``created_by`` is the ``AppUser.id`` of the admin generating the token.

    Returns:
        A URL-safe random token string (32 bytes, ~43 chars base64url).

    Raises:
        RepresentativeNotFoundError: no matching representative.
        PlatformNotFoundError: no matching platform.
    """
    rep = session.get(Representative, representative_id)
    if rep is None:
        raise RepresentativeNotFoundError(representative_id)

    platform = _get_platform(session, platform_code)

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now(timezone.utc) + _BINDING_TOKEN_TTL

    bot_binding_token = BotBindingToken(
        token_hash=token_hash,
        representative_id=representative_id,
        bot_platform_id=platform.id,
        expires_at=expires_at,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(bot_binding_token)
    session.flush()
    return raw_token


def _consume_binding_token(
    session: Session,
    token: str,
    *,
    consumed_by: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Validate and consume a binding token.  Raises on invalid/expired/used.

    Returns:
        (representative_id, bot_platform_id) on success.
    """
    token_hash = _hash_token(token)

    row = session.execute(
        select(BotBindingToken).where(BotBindingToken.token_hash == token_hash)
    ).scalar_one_or_none()

    if row is None:
        raise InvalidBindingTokenError(token)

    if row.consumed_at is not None:
        raise InvalidBindingTokenError(token)

    if datetime.now(timezone.utc) > row.expires_at:
        raise InvalidBindingTokenError(token)

    # Mark consumed.
    row.consumed_at = datetime.now(timezone.utc)
    row.consumed_by = consumed_by
    row.updated_by = consumed_by
    session.flush()

    return row.representative_id, row.bot_platform_id


# ---------------------------------------------------------------------------
# Platform lookup
# ---------------------------------------------------------------------------

def _get_platform(session: Session, platform_code: str) -> BotPlatformRef:
    """Return the ``BotPlatformRef`` for ``platform_code``, or raise."""
    platform = session.execute(
        select(BotPlatformRef).where(BotPlatformRef.code == platform_code)
    ).scalar_one_or_none()
    if platform is None:
        raise PlatformNotFoundError(platform_code)
    return platform


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def get_or_create_session(
    session: Session,
    *,
    platform_code: str,
    platform_user_id: str,
) -> BotSession:
    """Return the active LINKED session for a platform identity.

    This is the function called on every incoming bot message.  If no
    session exists, it raises ``SessionNotLinkedError`` -- callers should
    prompt the user to bind first.

    If the session exists but is REVOKED or EXPIRED, it also raises
    ``SessionNotLinkedError``.
    """
    platform = _get_platform(session, platform_code)
    existing = session.execute(
        select(BotSession).where(
            BotSession.bot_platform_id == platform.id,
            BotSession.platform_user_id == platform_user_id,
        )
    ).scalar_one_or_none()
    if existing is None or existing.status != "LINKED":
        raise SessionNotLinkedError(platform_code, platform_user_id)
    return existing


def resolve_session(
    session: Session,
    *,
    platform_code: str,
    platform_user_id: str,
) -> BotSession | None:
    """Return the active LINKED session, or ``None`` if unlinked/revoked."""
    platform = _get_platform(session, platform_code)
    existing = session.execute(
        select(BotSession).where(
            BotSession.bot_platform_id == platform.id,
            BotSession.platform_user_id == platform_user_id,
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "LINKED":
        return existing
    return None


def create_binding(
    session: Session,
    *,
    binding_token: str,
    platform_code: str,
    platform_user_id: str,
    linked_by: uuid.UUID,
) -> BotSession:
    """Create a LINKED bot session from a validated binding token.

    This is the identity-binding entry point.  The caller must hold
    ``BOT_MANAGE`` permission.

    The binding token is consumed (single-use).  The resulting
    ``BotSession`` records the ``(platform, platform_user_id)`` pair
    bound to the ``Representative`` embedded in the token.

    Raises:
        InvalidBindingTokenError: token invalid/expired.
        SessionAlreadyLinkedError: platform user already has a LINKED session.
        PlatformNotFoundError: platform code doesn't exist.
        RepresentativeNotFoundError: representative doesn't exist.
    """
    representative_id, bot_platform_id = _consume_binding_token(
        session, binding_token, consumed_by=linked_by,
    )

    # Check for existing linked session (unique constraint would reject
    # at DB level, but we want a clear error message).
    existing = session.execute(
        select(BotSession).where(
            BotSession.bot_platform_id == bot_platform_id,
            BotSession.platform_user_id == platform_user_id,
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "LINKED":
        raise SessionAlreadyLinkedError(platform_code, platform_user_id)

    # Generate a session token for the new binding.
    session_token = secrets.token_urlsafe(32)

    if existing is not None:
        # Re-link: update the existing row (one platform user <-> one representative).
        existing.representative_id = representative_id
        existing.status = "LINKED"
        existing.session_token = session_token
        existing.linked_at = datetime.now(timezone.utc)
        existing.updated_by = linked_by
        session.flush()
        return existing

    # New binding.
    bot_session = BotSession(
        representative_id=representative_id,
        bot_platform_id=bot_platform_id,
        platform_user_id=platform_user_id,
        status="LINKED",
        session_token=session_token,
        created_by=linked_by,
        updated_by=linked_by,
    )
    session.add(bot_session)
    session.flush()
    return bot_session


def revoke_session(
    session: Session,
    *,
    platform_code: str,
    platform_user_id: str,
    revoked_by: uuid.UUID,
) -> BotSession:
    """Revoke (unlink) a bot session.  Sets ``status = REVOKED``.

    The caller must hold ``BOT_MANAGE`` permission.

    Raises:
        SessionNotLinkedError: no active linked session exists.
    """
    bot_session = get_or_create_session(
        session, platform_code=platform_code, platform_user_id=platform_user_id,
    )
    bot_session.status = "REVOKED"
    bot_session.updated_by = revoked_by
    session.flush()
    return bot_session


def get_session_by_id(session: Session, session_id: uuid.UUID) -> BotSession | None:
    """Return a ``BotSession`` by its primary key, or ``None``."""
    return session.get(BotSession, session_id)


# ---------------------------------------------------------------------------
# Message logging (append-only)
# ---------------------------------------------------------------------------

def log_inbound(
    session: Session,
    *,
    bot_session_id: uuid.UUID,
    platform_code: str,
    raw_payload: dict,
    command_parsed: str | None = None,
) -> BotMessageLog:
    """Log an inbound bot message (append-only).

    ``raw_payload`` is the full platform-specific message payload (e.g.
    Telegram's ``Update`` object serialized to dict).
    """
    platform = _get_platform(session, platform_code)
    entry = BotMessageLog(
        bot_session_id=bot_session_id,
        bot_platform_id=platform.id,
        direction="INBOUND",
        raw_payload=raw_payload,
        command_parsed=command_parsed,
    )
    session.add(entry)
    session.flush()
    return entry


def log_outbound(
    session: Session,
    *,
    bot_session_id: uuid.UUID,
    platform_code: str,
    raw_payload: dict,
    command_parsed: str | None = None,
) -> BotMessageLog:
    """Log an outbound bot message (append-only)."""
    platform = _get_platform(session, platform_code)
    entry = BotMessageLog(
        bot_session_id=bot_session_id,
        bot_platform_id=platform.id,
        direction="OUTBOUND",
        raw_payload=raw_payload,
        command_parsed=command_parsed,
    )
    session.add(entry)
    session.flush()
    return entry


__all__ = [
    "InvalidBindingTokenError",
    "PlatformNotFoundError",
    "RepresentativeNotFoundError",
    "SessionAlreadyLinkedError",
    "SessionNotLinkedError",
    "create_binding",
    "generate_binding_token",
    "get_or_create_session",
    "get_session_by_id",
    "log_inbound",
    "log_outbound",
    "resolve_session",
    "revoke_session",
]
