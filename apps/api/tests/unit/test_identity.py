"""Pure-Python unit test for the Issue #17 login-identifier normalization
helper — no database required. Database-level invariants (uniqueness of
the normalized value, the generated column, etc.) are covered in
tests/integration/test_identity.py.
"""

from app.db.identity import normalize_login_identifier


def test_normalize_login_identifier_trims_and_lowercases() -> None:
    assert normalize_login_identifier("  Mixed.Case@Example.COM  ") == "mixed.case@example.com"


def test_normalize_login_identifier_is_stable_for_already_normalized_values() -> None:
    assert normalize_login_identifier("already@normal.com") == "already@normal.com"
