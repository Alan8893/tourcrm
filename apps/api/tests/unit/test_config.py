"""Proves the test/config layer never falls back to a real database — the
Issue #7 acceptance criterion that tests do not depend on production
database or production secrets. Pure unit test: no network, no DB.
"""

import pytest

from app.core.config import ConfigurationError, get_settings


def test_get_settings_has_no_hardcoded_database_fallback(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ConfigurationError):
        get_settings()


def test_get_settings_uses_only_the_environment_value(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/synthetic_test_db")

    settings = get_settings()

    assert settings.database_url == "postgresql+psycopg://u:p@localhost:5432/synthetic_test_db"
