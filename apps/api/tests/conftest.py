"""Shared, technical-only fixtures for the whole backend test suite
(Issue #7). Nothing here encodes a business/domain model — see
tests/factories.py for the underlying helpers.
"""

import pytest

from tests.factories import unique_id, unique_token


@pytest.fixture
def technical_id() -> str:
    """A fresh opaque identifier for tests that just need *some* unique,
    non-business value (e.g. a technical probe key)."""
    return unique_id()


@pytest.fixture
def technical_token() -> str:
    """A fresh synthetic, non-secret placeholder string."""
    return unique_token()
