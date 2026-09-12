"""Foundation unit tests for the technical test factories themselves."""

from tests.factories import unique_id, unique_token


def test_unique_id_returns_distinct_values() -> None:
    assert unique_id() != unique_id()


def test_unique_token_has_expected_shape() -> None:
    token = unique_token(prefix="probe")
    assert token.startswith("probe-")
    assert len(token) == len("probe-") + 12


def test_technical_id_fixture_is_a_non_empty_string(technical_id: str) -> None:
    assert isinstance(technical_id, str)
    assert technical_id
