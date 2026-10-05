"""Official Difficulty validation (Issue #268,
docs/04-modules/trips-and-tourist-profile.md §4): every approved
mode/value/source combination is accepted and every other one rejected
by the authoritative backend check."""

import pytest

from app.trips.official_difficulty import (
    CATEGORY_VALUES,
    DEGREE_VALUES,
    InvalidOfficialDifficultyError,
    OfficialDifficulty,
    build_official_difficulty,
)

_ALL_VALUES = ("I", "II", "III", "IV", "V", "VI", "VII", "0", "")
_SOURCE = "Решение МКК № 12"


@pytest.mark.parametrize("value", DEGREE_VALUES)
def test_degree_values_with_source(value: str) -> None:
    assert build_official_difficulty(mode="DEGREE", value=value, source=_SOURCE) == (
        OfficialDifficulty(mode="DEGREE", value=value, source=_SOURCE)
    )


@pytest.mark.parametrize("value", CATEGORY_VALUES)
def test_category_values_with_source(value: str) -> None:
    assert build_official_difficulty(mode="CATEGORY", value=value, source=_SOURCE) == (
        OfficialDifficulty(mode="CATEGORY", value=value, source=_SOURCE)
    )


def test_weekend_without_value_with_source() -> None:
    assert build_official_difficulty(mode="WEEKEND", value=None, source=_SOURCE) == (
        OfficialDifficulty(mode="WEEKEND", value=None, source=_SOURCE)
    )


@pytest.mark.parametrize("source", [None, "", "   "])
def test_none_without_value_needs_no_source(source: str | None) -> None:
    assert build_official_difficulty(mode="NONE", value=None, source=source) == (
        OfficialDifficulty(mode="NONE", value=None, source=None)
    )


def test_none_keeps_an_optional_source() -> None:
    assert build_official_difficulty(mode="NONE", value=None, source=f"  {_SOURCE} ").source == (
        _SOURCE
    )


@pytest.mark.parametrize("mode", ["NONE", "WEEKEND"])
@pytest.mark.parametrize("value", _ALL_VALUES)
def test_modes_without_value_reject_any_value(mode: str, value: str) -> None:
    with pytest.raises(InvalidOfficialDifficultyError):
        build_official_difficulty(mode=mode, value=value, source=_SOURCE)


@pytest.mark.parametrize(
    ("mode", "allowed"), [("DEGREE", DEGREE_VALUES), ("CATEGORY", CATEGORY_VALUES)]
)
def test_valued_modes_reject_missing_and_unapproved_values(
    mode: str, allowed: tuple[str, ...]
) -> None:
    with pytest.raises(InvalidOfficialDifficultyError):
        build_official_difficulty(mode=mode, value=None, source=_SOURCE)
    for value in _ALL_VALUES:
        if value in allowed:
            continue
        with pytest.raises(InvalidOfficialDifficultyError):
            build_official_difficulty(mode=mode, value=value, source=_SOURCE)


def test_degree_iv_and_category_vii_are_rejected() -> None:
    with pytest.raises(InvalidOfficialDifficultyError):
        build_official_difficulty(mode="DEGREE", value="IV", source=_SOURCE)
    with pytest.raises(InvalidOfficialDifficultyError):
        build_official_difficulty(mode="CATEGORY", value="VII", source=_SOURCE)


@pytest.mark.parametrize(
    ("mode", "value"), [("DEGREE", "II"), ("CATEGORY", "VI"), ("WEEKEND", None)]
)
@pytest.mark.parametrize("source", [None, "", "   "])
def test_source_required_except_for_none(mode: str, value: str | None, source: str | None) -> None:
    with pytest.raises(InvalidOfficialDifficultyError):
        build_official_difficulty(mode=mode, value=value, source=source)


@pytest.mark.parametrize("mode", ["", "none", "degree", "OTHER", "CLUB"])
def test_unknown_modes_are_rejected(mode: str) -> None:
    with pytest.raises(InvalidOfficialDifficultyError):
        build_official_difficulty(mode=mode, value=None, source=_SOURCE)


def test_overlong_source_is_rejected() -> None:
    with pytest.raises(InvalidOfficialDifficultyError):
        build_official_difficulty(mode="WEEKEND", value=None, source="x" * 501)
    assert build_official_difficulty(mode="WEEKEND", value=None, source="x" * 500).source
