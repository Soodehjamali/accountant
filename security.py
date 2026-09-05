"""Password hashing and JWT (HS256) helpers -- stdlib-only.

Lives at the project root (a sibling of ``database/`` and ``services/``),
NOT under ``backend/app/``, even though today's only caller
(``services/auth_service.py``) is itself only consumed by the backend.
This mirrors the existing rule that ``services/`` never depends on
``backend/`` -- the opposite direction is fine and is exactly how
``database.session`` already works (``backend/`` consumes it, it doesn't
live inside ``backend/``). A future desktop UI or CLI script that needs to
hash a password or mint a token can import this module directly, the same
way ``services/product_service.py`` is reused unchanged by both a script
and a future API endpoint (see ``services/__init__.py``'s own docstring).

Deliberately does NOT depend on ``passlib``/``bcrypt`` or ``python-jose``/
``PyJWT``. Both are perfectly reasonable choices for a real project, but
adding either here would be an untested dependency bump (this module was
written and verified in an environment with no package-install access), so
this uses only what ``hashlib``/``hmac``/``secrets``/``base64`` already give
us for free:

* Password hashing: PBKDF2-HMAC-SHA256 (``hashlib.pbkdf2_hmac``) -- a
  NIST-approved KDF available in the stdlib since Python 3.4, no native
  extension required (unlike bcrypt/argon2, which need a compiled wheel).
* Tokens: a minimal, dependency-free HS256 JWT encoder/decoder. It only
  implements what this app needs (HS256, ``exp`` claim checking) -- it is
  NOT a general-purpose JWT library and does not attempt to be one.

If the project later adds real network/pip access and wants
argon2/bcrypt or a full JWT library, swapping the *internals* of
``hash_password``/``verify_password`` or ``create_access_token``/
``decode_access_token`` is a self-contained change -- nothing outside this
module inspects the token/hash format directly (see ``services/
auth_service.py``, which only calls these functions).

Storage note: ``AppUser.password_hash`` is ``VARCHAR(120)``
(``database/types.py``'s ``token_type()``). The encoded format produced by
``hash_password`` is::

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

With ``PBKDF2_ITERATIONS`` at 6 digits, a 16-byte salt (32 hex chars), and a
32-byte SHA-256 digest (64 hex chars), this is 118 characters -- 2 under the
120 cap. If ``PBKDF2_ITERATIONS`` is ever raised to 7 digits, recheck this
budget (see the module-level assertion at the bottom of this file, which
fails loudly at import time rather than silently truncating a hash on
insert).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256)
# --------------------------------------------------------------------------

_PBKDF2_ALGORITHM = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 260_000  # OWASP (2023) minimum recommendation for PBKDF2-SHA256
_SALT_BYTES = 16


def hash_password(plain_password: str) -> str:
    """Return a salted PBKDF2-HMAC-SHA256 hash of ``plain_password``.

    A fresh random salt is generated on every call (via ``secrets.
    token_bytes``, a CSPRNG), so hashing the same password twice produces
    two different, equally valid encoded hashes.
    """

    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plain_password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"{_PBKDF2_ALGORITHM}${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(plain_password: str, encoded_hash: str) -> bool:
    """Return ``True`` iff ``plain_password`` matches ``encoded_hash``.

    Never raises on a malformed ``encoded_hash`` (e.g. a legacy/placeholder
    value like ``bootstrap_service``'s ``"not-a-real-hash"``) -- returns
    ``False`` instead, since "this credential doesn't check out" and
    "this credential is stored in a format we can't even parse" should
    both simply mean "login denied" to the caller, not a 500 error.
    """

    try:
        algorithm, iterations_str, salt_hex, hash_hex = encoded_hash.split("$")
        if algorithm != _PBKDF2_ALGORITHM:
            return False
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256", plain_password.encode("utf-8"), salt, iterations
    )
    # Constant-time comparison -- avoids leaking hash-match progress via
    # response-timing side channels.
    return hmac.compare_digest(actual, expected)


# --------------------------------------------------------------------------
# JWT (HS256 only) -- minimal, dependency-free
# --------------------------------------------------------------------------


class InvalidTokenError(ValueError):
    """Raised by :func:`decode_access_token` for any invalid/expired token.

    Deliberately a single exception type (not one per failure mode) --
    callers (the ``get_current_user`` FastAPI dependency, in a later task)
    only ever need to do one thing on any of these failures: reject the
    request with 401. Distinguishing "expired" from "tampered" from
    "malformed" to the client would leak information useful to an attacker
    probing the auth boundary, for no benefit to a legitimate caller.
    """


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(
    *, subject: str, secret_key: str, expires_in_seconds: int, extra_claims: dict | None = None
) -> str:
    """Return a signed HS256 JWT with ``sub``, ``iat``, and ``exp`` claims.

    Args:
        subject: the JWT ``sub`` claim -- this app always passes the
          ``AppUser.id`` (as a string), never the username/email, so a
          later username change can't silently invalidate/misdirect
          already-issued tokens.
        secret_key: the HMAC signing key (``Settings.secret_key``).
        expires_in_seconds: token lifetime from "now".
        extra_claims: optional additional claims merged into the payload
          (e.g. ``{"role": "admin"}``) -- none are used yet in this task,
          but the signature accepts them so a later task can add
          role/permission claims without reshaping this function.
    """

    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + expires_in_seconds,
        **(extra_claims or {}),
    }

    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


@dataclass(frozen=True)
class TokenPayload:
    """Decoded, verified claims from an access token."""

    subject: str
    issued_at: int
    expires_at: int
    #: Any additional signed claims (e.g. ``{"type": "bot", "session_id": ...}``
    #: for bot tokens).  Defaults to ``{}`` -- legacy tokens / app tokens have
    #: none.  Callers must treat this as untrusted-free only after signature
    #: verification (which ``decode_access_token`` performs before returning).
    extra_claims: dict = field(default_factory=dict)


def decode_access_token(token: str, *, secret_key: str) -> TokenPayload:
    """Verify ``token``'s signature and expiry, and return its claims.

    Raises:
        InvalidTokenError: if the token is malformed, the signature does
          not match, the algorithm isn't HS256, or ``exp`` has passed.
    """

    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise InvalidTokenError("Token is not a three-part JWT.") from exc

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_signature = hmac.new(
        secret_key.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    try:
        actual_signature = _b64url_decode(signature_b64)
    except Exception as exc:  # noqa: BLE001 - any decode failure -> invalid token
        raise InvalidTokenError("Token signature is not valid base64url.") from exc

    if not hmac.compare_digest(expected_signature, actual_signature):
        raise InvalidTokenError("Token signature does not match.")

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:  # noqa: BLE001 - any decode failure -> invalid token
        raise InvalidTokenError("Token header/payload is not valid JSON.") from exc

    if header.get("alg") != "HS256":
        raise InvalidTokenError(f"Unsupported algorithm: {header.get('alg')!r}.")

    try:
        subject = payload["sub"]
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenError("Token payload is missing required claims.") from exc

    if time.time() >= expires_at:
        raise InvalidTokenError("Token has expired.")

    # Everything else that was signed into the payload travels along as
    # extra claims.  ``dict(payload)`` is safe: the three required claims are
    # already popped into typed fields, and the payload was verified.
    extra = dict(payload)
    extra.pop("sub", None)
    extra.pop("iat", None)
    extra.pop("exp", None)

    return TokenPayload(
        subject=subject,
        issued_at=issued_at,
        expires_at=expires_at,
        extra_claims=extra,
    )


__all__ = [
    "InvalidTokenError",
    "TokenPayload",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "verify_password",
]

# Fail loudly at import time if the encoded-hash format budget (see module
# docstring) is ever silently blown by a future edit -- 120 is
# AppUser.password_hash's VARCHAR width (database/types.py's token_type()).
assert len(hash_password("budget-check")) <= 120, (
    "hash_password() output exceeds AppUser.password_hash's VARCHAR(120) "
    "column width -- see this module's docstring for the byte budget."
)
