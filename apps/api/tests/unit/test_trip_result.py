"""Trip Result (Issue #276, trips-and-tourist-profile.md §8): the
approved values and the absence of a Result."""

import pytest

from app.trips.result import TRIP_RESULTS, InvalidTripResultError, validate_trip_result


def test_the_three_approved_values() -> None:
    assert TRIP_RESULTS == ("COMPLETED", "PARTIALLY_COMPLETED", "NOT_COMPLETED")


@pytest.mark.parametrize("value", ["COMPLETED", "PARTIALLY_COMPLETED", "NOT_COMPLETED"])
def test_approved_values_are_valid(value: str) -> None:
    assert validate_trip_result(value) == value


def test_absence_is_valid() -> None:
    assert validate_trip_result(None) is None


@pytest.mark.parametrize(
    "value",
    ["", "completed", "CANCELLED", "PARTIAL", "FAILED", "UNCLASSIFIED", "DONE", " COMPLETED"],
)
def test_other_values_are_rejected(value: str) -> None:
    with pytest.raises(InvalidTripResultError):
        validate_trip_result(value)
