"""Environment-driven application configuration.

No default carries a real credential. `DATABASE_URL` must be supplied by the
environment (locally via a gitignored `.env`, in CI/production via secret
management) — see `apps/api/.env.example`.
"""

import os
from dataclasses import dataclass


class ConfigurationError(RuntimeError):
    """Raised when required environment configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    # auth-api.md §19 / ADR-0009: session/CSRF cookies need `Secure` in an
    # Internet deployment, but a plain-HTTP LAN/local-dev deployment (also
    # explicitly anticipated by §19) cannot set it. No specific deployment
    # profile is canonical, so this is deployment configuration (default
    # "secure", matching the documented default expectation), not a
    # hardcoded assumption baked into the cookie-issuing code itself.
    cookie_secure: bool = True


def get_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ConfigurationError(
            "DATABASE_URL environment variable is not set. "
            "See apps/api/.env.example for the expected format."
        )
    cookie_secure = os.getenv("COOKIE_SECURE", "true").strip().lower() not in ("false", "0", "no")
    return Settings(database_url=database_url, cookie_secure=cookie_secure)
