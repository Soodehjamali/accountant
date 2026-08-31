"""Application-level settings for the Enterprise ERP backend.

Scope: app name/version, API prefix, debug flag, CORS origins, JWT auth
settings. Deliberately does NOT declare ``DATABASE_URL`` -- that remains
``database.session``'s own responsibility (its module docstring: "the only
place connection credentials / host / db name may be supplied"). This
module only ensures a local ``.env`` file (if present) is loaded *before*
anything imports ``database.session``, so ``DATABASE_URL`` set there is
visible either way.

Uses ``pydantic-settings`` (the Pydantic v2 split-out settings package) --
not bundled with plain ``pydantic`` v2 the way it was in v1.
"""

from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env as early as possible (idempotent; a no-op if the file is
# absent or the vars are already exported in the shell) -- so
# DATABASE_URL, if only defined in .env, is present in os.environ by the
# time database.session.get_engine() first reads it.
load_dotenv()

#: Sentinel default for ``secret_key`` -- deliberately obvious and
#: deliberately NOT a plausible real secret, so it's unmistakable in a
#: diff/config dump. See :func:`get_settings` for the guard that refuses
#: to start with this value when ``environment="production"``.
_INSECURE_DEFAULT_SECRET_KEY = "INSECURE-DEV-SECRET-CHANGE-ME-BEFORE-PRODUCTION"


class Settings(BaseSettings):
    """Application settings, overridable via environment variables/.env.

    Every field below can be set via an environment variable of the same
    (case-insensitive) name, e.g. ``APP_NAME``, ``DEBUG``, ``API_V1_PREFIX``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Enterprise ERP (SIWRMS) API"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False

    #: Prefix every versioned route is mounted under (requirement #9).
    api_v1_prefix: str = "/api/v1"

    #: CORS origins allowed to call this API. Includes the Vite dev server
    #: origin for frontend development; widen further for production deployment.
    cors_origins: list[str] = ["http://localhost:5173"]

    #: HMAC signing key for access tokens (``security.create_access_token``
    #: / ``security.decode_access_token``). Defaults to an obviously-fake
    #: dev value -- :func:`get_settings` refuses to boot with this default
    #: when ``environment="production"`` (see the check there). Override
    #: via the ``SECRET_KEY`` environment variable / ``.env`` entry.
    secret_key: str = _INSECURE_DEFAULT_SECRET_KEY

    #: Access token lifetime. 8 hours -- long enough that an office/rep
    #: user logging in once in the morning doesn't get logged out
    #: mid-shift, short enough that a leaked token has a bounded window.
    #: Revisit once refresh tokens (out of scope for this task) exist.
    access_token_expire_minutes: int = 60 * 8


@lru_cache
def get_settings() -> Settings:
    """Return the memoized, process-wide :class:`Settings` instance.

    Raises:
        RuntimeError: if ``environment="production"`` and ``secret_key``
          is still the insecure default -- refusing to start is safer
          than silently signing every login token with a value published
          in this repo's own source code.
    """

    settings = Settings()
    if (
        settings.environment == "production"
        and settings.secret_key == _INSECURE_DEFAULT_SECRET_KEY
    ):
        raise RuntimeError(
            "SECRET_KEY is unset (still the insecure default) while "
            "ENVIRONMENT=production. Set a real SECRET_KEY via the "
            "environment or .env before starting in production."
        )
    return settings


__all__ = ["Settings", "get_settings"]
