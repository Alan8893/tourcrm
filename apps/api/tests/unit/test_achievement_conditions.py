"""Unit tests for structured Achievement Rule conditions (Issue #220,
A5/A8) — app.achievements.conditions. Pure: no database."""

import pytest

from app.achievements.conditions import (
    MAX_DEPTH,
    RuleConditionError,
    UnsupportedMetricError,
    evaluate_condition,
    referenced_metrics,
    validate_condition,
)
from app.achievements.metrics import APPROVED_METRIC_CODES, COMPLETED_TRIPS

APPROVED = frozenset({"metric_a", "metric_b", "metric_c", "metric_d"})


def _leaf(metric: str, operator: str, value: int) -> dict:
    return {"metric": metric, "operator": operator, "value": value}


def _and(*children: dict) -> dict:
    return {"logic": "AND", "conditions": list(children)}


def test_approved_catalog_is_exactly_completed_trips() -> None:
    assert APPROVED_METRIC_CODES == frozenset({COMPLETED_TRIPS})


# --- simple condition and comparison operators --------------------------------------


@pytest.mark.parametrize(
    ("operator", "value", "actual", "expected"),
    [
        (">=", 2, 2, True),
        (">=", 2, 1, False),
        (">", 2, 3, True),
        (">", 2, 2, False),
        ("==", 2, 2, True),
        ("==", 2, 3, False),
        ("<=", 2, 2, True),
        ("<=", 2, 3, False),
        ("<", 2, 1, True),
        ("<", 2, 2, False),
    ],
)
def test_simple_condition_comparison_operators(
    operator: str, value: int, actual: int, expected: bool
) -> None:
    node = _leaf("metric_a", operator, value)
    validate_condition(node, approved_metrics=APPROVED)
    assert evaluate_condition(node, {"metric_a": actual}) is expected


# --- AND / OR / nesting -----------------------------------------------------------


def test_and_requires_every_child() -> None:
    node = {"logic": "AND", "conditions": [_leaf("metric_a", ">=", 1), _leaf("metric_b", ">=", 2)]}
    validate_condition(node, approved_metrics=APPROVED)
    assert evaluate_condition(node, {"metric_a": 1, "metric_b": 2}) is True
    assert evaluate_condition(node, {"metric_a": 1, "metric_b": 1}) is False


def test_or_requires_one_child() -> None:
    node = {"logic": "OR", "conditions": [_leaf("metric_a", ">=", 5), _leaf("metric_b", ">=", 2)]}
    validate_condition(node, approved_metrics=APPROVED)
    assert evaluate_condition(node, {"metric_a": 0, "metric_b": 2}) is True
    assert evaluate_condition(node, {"metric_a": 0, "metric_b": 1}) is False


def test_nested_and_inside_and() -> None:
    node = {
        "logic": "AND",
        "conditions": [
            _leaf("metric_a", ">=", 1),
            _and(_leaf("metric_b", ">=", 1), _leaf("metric_c", ">=", 1)),
        ],
    }
    validate_condition(node, approved_metrics=APPROVED)
    assert evaluate_condition(node, {"metric_a": 1, "metric_b": 1, "metric_c": 1}) is True
    assert evaluate_condition(node, {"metric_a": 1, "metric_b": 1, "metric_c": 0}) is False


def test_nested_or_inside_or() -> None:
    node = {
        "logic": "OR",
        "conditions": [
            _leaf("metric_a", ">=", 9),
            {"logic": "OR", "conditions": [_leaf("metric_b", ">=", 9), _leaf("metric_c", ">=", 1)]},
        ],
    }
    validate_condition(node, approved_metrics=APPROVED)
    assert evaluate_condition(node, {"metric_a": 0, "metric_b": 0, "metric_c": 1}) is True
    assert evaluate_condition(node, {"metric_a": 0, "metric_b": 0, "metric_c": 0}) is False


def test_nested_or_of_and_groups_as_in_canonical_example() -> None:
    # achievements-and-norms.md §20's OR( AND(..), AND(..) ) shape.
    node = {
        "logic": "OR",
        "conditions": [
            _and(_leaf("metric_a", ">=", 1), _leaf("metric_b", ">=", 2)),
            _and(_leaf("metric_c", ">=", 1), _leaf("metric_d", ">=", 3)),
        ],
    }
    validate_condition(node, approved_metrics=APPROVED)
    assert referenced_metrics(node) == APPROVED
    first_path = {"metric_a": 1, "metric_b": 2, "metric_c": 0, "metric_d": 0}
    second_path = {"metric_a": 0, "metric_b": 0, "metric_c": 1, "metric_d": 3}
    neither = {"metric_a": 1, "metric_b": 1, "metric_c": 1, "metric_d": 2}
    assert evaluate_condition(node, first_path) is True
    assert evaluate_condition(node, second_path) is True
    assert evaluate_condition(node, neither) is False


# --- missing facts ----------------------------------------------------------------


def test_missing_fact_never_satisfies_a_condition() -> None:
    assert evaluate_condition(_leaf("metric_a", ">=", 0), {}) is False
    assert evaluate_condition(_leaf("metric_a", "<", 5), {"metric_a": None}) is False


def test_missing_fact_inside_or_is_false_not_true() -> None:
    node = {"logic": "OR", "conditions": [_leaf("metric_a", ">=", 0), _leaf("metric_b", ">=", 9)]}
    assert evaluate_condition(node, {"metric_b": 1}) is False


def test_missing_fact_inside_and_makes_the_group_false() -> None:
    node = {"logic": "AND", "conditions": [_leaf("metric_a", ">=", 0), _leaf("metric_b", ">=", 0)]}
    assert evaluate_condition(node, {"metric_a": 5}) is False


# --- validation -------------------------------------------------------------------


def test_unsupported_metric_is_rejected_even_when_nested() -> None:
    node = {
        "logic": "AND",
        "conditions": [
            _leaf(COMPLETED_TRIPS, ">=", 1),
            {"logic": "OR", "conditions": [_leaf("tourism_regions", ">=", 1)]},
        ],
    }
    with pytest.raises(UnsupportedMetricError) as exc_info:
        validate_condition(node, approved_metrics=APPROVED_METRIC_CODES)
    assert exc_info.value.metric == "tourism_regions"
    assert exc_info.value.path == "$.conditions[1].conditions[0]"


@pytest.mark.parametrize(
    "metric",
    [
        "overnights",
        "one_day_hikes",
        "multi_day_hikes",
        "degree_hikes",
        "category_hikes",
        "tourism_types",
        "tourism_regions",
    ],
)
def test_every_non_approved_canonical_metric_is_unsupported(metric: str) -> None:
    with pytest.raises(UnsupportedMetricError):
        validate_condition(_leaf(metric, ">=", 1), approved_metrics=APPROVED_METRIC_CODES)


@pytest.mark.parametrize(
    "node",
    [
        [],
        "x",
        {},
        {"logic": "XOR", "conditions": [_leaf("metric_a", ">=", 1)]},
        {"logic": "AND", "conditions": []},
        {"logic": "AND"},
        {"logic": "AND", "conditions": [_leaf("metric_a", ">=", 1)], "extra": 1},
        {"metric": "metric_a", "operator": "!=", "value": 1},
        {"metric": "metric_a", "operator": ">=", "value": -1},
        {"metric": "metric_a", "operator": ">=", "value": 1.5},
        {"metric": "metric_a", "operator": ">=", "value": True},
        {"metric": "metric_a", "operator": ">="},
        {"metric": "", "operator": ">=", "value": 1},
    ],
)
def test_malformed_conditions_are_rejected(node: object) -> None:
    with pytest.raises(RuleConditionError):
        validate_condition(node, approved_metrics=APPROVED)


def test_depth_is_bounded() -> None:
    node: dict = _leaf("metric_a", ">=", 1)
    for _ in range(MAX_DEPTH):
        node = {"logic": "AND", "conditions": [node]}
    with pytest.raises(RuleConditionError):
        validate_condition(node, approved_metrics=APPROVED)
