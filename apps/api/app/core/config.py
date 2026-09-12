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


def get_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ConfigurationError(
            "DATABASE_URL environment variable is not set. "
            "See apps/api/.env.example for the expected format."
        )
    return Settings(database_url=database_url)
