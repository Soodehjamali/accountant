"""Phone-based bot authentication service.

Handles the phone verification flow for bot users (ADR-013 REST/JWT
architecture):
1. Look up phone number in ``representative_contact`` (kind=PHONE)
2. Verify the representative is ACTIVE
3. Create/update a persistent ``BotSession`` binding the platform identity
   (``bot_session_service.bind_phone_verified_session``) -- the session
   survives bot-process restarts because it lives in PostgreSQL, never in
   an in-memory dict
4. Return a short-lived JWT (30 minutes) carrying both the
   ``representative_id`` (sub) and the ``bot_session`` id (``session_id``
   claim) so the bot-auth dependency can reject revoked/expired sessions
   immediately instead of waiting for JWT expiry

This service does NOT check permissions -- that is the caller's
responsibility (the API endpoint or the bot dependency).
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.representative import Representative
from database.models.representative_contact import RepresentativeContact
from security import create_access_token
from services import bot_session_service


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: JWT lifetime for bot tokens -- 30 minutes.
_BOT_TOKEN_EXPIRE_SECONDS = 30 * 60

#: Default lifetime of a phone-verified bot session binding.  Long enough
#: that the representative is not forced through phone re-verification too
#: often, short enough that an abandoned binding expires.  Refresh happens
#: naturally: every ``/start`` + phone share re-binds and re-arms the clock.
_SESSION_TTL = datetime.timedelta(days=90)

#: Known platform codes (matches ``BotPlatformRef.code`` values).
_VALID_PLATFORMS = frozenset({"TELEGRAM", "BALE"})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PhoneNotFoundError(LookupError):
    """Raised when the phone number is not found in ``representative_contact``."""

    def __init__(self, phone_number: str) -> None:
        super().__init__(
            f"Phone number '{phone_number}' is not registered to any representative."
        )
        self.phone_number = phone_number


class RepresentativeInactiveError(ValueError):
    """Raised when the representative found is not ACTIVE."""

    def __init__(self, representative_id: uuid.UUID, status: str) -> None:
        super().__init__(
            f"Representative '{representative_id}' is not active (status={status})."
        )
        self.representative_id = representative_id
        self.status = status


class InvalidPlatformError(ValueError):
    """Raised when an unsupported platform code is provided."""

    def __init__(self, platform: str) -> None:
        super().__init__(
            f"Unsupported platform '{platform}'. Must be one of: {sorted(_VALID_PLATFORMS)}"
        )
        self.platform = platform


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhoneVerificationResult:
    """Result of a successful phone verification."""

    representative_id: uuid.UUID
    representative_name: str
    access_token: str
    expires_in: int


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

def verify_phone(
    db: Session,
    *,
    phone_number: str,
    platform: str,
    chat_id: str,
    secret_key: str,
) -> PhoneVerificationResult:
    """Verify a phone number and return a bot access token.

    1. Normalizes the phone number (strips spaces, dashes, ensures +98 prefix).
    2. Looks up the phone in ``representative_contact`` (kind=PHONE).
    3. Verifies the representative is ACTIVE.
    4. Creates/updates the persistent ``BotSession`` binding the platform
       identity (``platform`` + ``chat_id``) to the verified representative.
    5. Returns a short-lived JWT containing ``rep_id`` and ``session_id``.

    Args:
        db: Open database session.
        phone_number: Phone number in any reasonable format.
        platform: Platform code ("TELEGRAM" or "BALE").
        chat_id: Platform-specific chat/user identifier -- the platform
          identity being bound (must be provided; it is never optional).
        secret_key: HMAC signing key for the JWT.

    Returns:
        PhoneVerificationResult with representative info and JWT token.

    Raises:
        PhoneNotFoundError: phone not in representative_contact.
        RepresentativeInactiveError: rep exists but is not ACTIVE.
        InvalidPlatformError: unsupported platform code.
    """
    if platform not in _VALID_PLATFORMS:
        raise InvalidPlatformError(platform)

    normalized_phone = _normalize_phone(phone_number)

    # 1. Look up the phone in representative_contact (kind=PHONE).
    contact = db.execute(
        select(RepresentativeContact).where(
            RepresentativeContact.kind == "PHONE",
            RepresentativeContact.value == normalized_phone,
            RepresentativeContact.deleted_at.is_(None),
        )
    ).scalar_one_or_none()

    if contact is None:
        # Try alternative phone formats (with/without +, with/without 0 prefix).
        alternatives = _alternative_phone_formats(normalized_phone)
        contact = db.execute(
            select(RepresentativeContact).where(
                RepresentativeContact.kind == "PHONE",
                RepresentativeContact.value.in_(alternatives),
                RepresentativeContact.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

    if contact is None:
        raise PhoneNotFoundError(phone_number)

    # 2. Load the representative and verify ACTIVE status.
    rep = db.get(Representative, contact.representative_id)
    if rep is None or rep.deleted_at is not None:
        raise PhoneNotFoundError(phone_number)

    if rep.status != "ACTIVE":
        raise RepresentativeInactiveError(rep.id, rep.status)

    # 3. Bind (or re-bind) the persistent bot session.  The actor for a
    #    self-service phone verification is the system user (there is no
    #    logged-in admin at this point in the flow).
    from services import bootstrap_service

    system_user = bootstrap_service.ensure_system_user(db)
    # A fresh database has no bot_platform_ref rows; seed them so the
    # platform lookup below cannot fail (idempotent).
    bootstrap_service.ensure_bot_platforms(db, system_user.id)
    bot_session = bot_session_service.bind_phone_verified_session(
        db,
        representative_id=rep.id,
        platform_code=platform,
        platform_user_id=chat_id,
        created_by=system_user.id,
        session_ttl=_SESSION_TTL,
    )

    # 4. Generate a short-lived JWT with the representative_id as subject
    #    and the bot_session id as a claim so auth can check revocation
    #    and expiry on every request.
    expires_in_seconds = _BOT_TOKEN_EXPIRE_SECONDS
    token = create_access_token(
        subject=str(rep.id),
        secret_key=secret_key,
        expires_in_seconds=expires_in_seconds,
        extra_claims={
            "type": "bot",
            "platform": platform,
            "session_id": str(bot_session.id),
        },
    )

    return PhoneVerificationResult(
        representative_id=rep.id,
        representative_name=rep.person_name,
        access_token=token,
        expires_in=expires_in_seconds,
    )


def _normalize_phone(phone: str) -> str:
    """Normalize a phone number to a canonical form.

    Strips spaces, dashes, parentheses. Ensures ``+`` prefix.
    Does NOT validate format -- that's the caller's concern.
    """
    phone = phone.strip()
    # Remove common formatting characters.
    for ch in (" ", "-", "(", ")", ".", "/"):
        phone = phone.replace(ch, "")
    # Ensure + prefix.
    if not phone.startswith("+"):
        if phone.startswith("0"):
            phone = "+98" + phone[1:]
        elif phone.startswith("98"):
            phone = "+" + phone
        else:
            phone = "+" + phone
    return phone


def _alternative_phone_formats(normalized: str) -> list[str]:
    """Generate alternative phone number formats for fuzzy matching.

    Iranian phone numbers have several common representations:
    - +989123456789 (canonical)
    - 09123456789 (local with 0 prefix)
    - 989123456789 (without +)
    - 9123456789 (without country code and 0)
    """
    formats = [normalized]

    # +98XXXXXXXXXX → 0XXXXXXXXXX
    if normalized.startswith("+98"):
        local = "0" + normalized[3:]
        formats.append(local)

    # +98XXXXXXXXXX → 98XXXXXXXXXX
    if normalized.startswith("+98"):
        formats.append("98" + normalized[2:])

    # +98XXXXXXXXXX → XXXXXXXXXX (without country code, with 0)
    if normalized.startswith("+98") and len(normalized) == 13:
        formats.append("0" + normalized[3:])

    # +98XXXXXXXXXX → XXXXXXXXXX (without country code, without 0)
    if normalized.startswith("+98") and len(normalized) == 13:
        formats.append(normalized[3:])

    # Also try with 912XXXXXXXXX (mobile without any prefix)
    if normalized.startswith("+98") and len(normalized) == 13:
        formats.append(normalized[3:])  # 912XXXXXXXXX

    return list(set(formats))  # deduplicate


__all__ = [
    "InvalidPlatformError",
    "PhoneNotFoundError",
    "PhoneVerificationResult",
    "RepresentativeInactiveError",
    "verify_phone",
]
