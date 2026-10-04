"""Structured Requirement / Rule conditions (Issue #220, A5/A8).

Canonical source: docs/04-modules/achievements-and-norms.md §7, §20, §23.

A Rule Version's `condition` is a tree of plain JSON data — never code:

    group:     {"logic": "AND" | "OR", "conditions": [<node>, ...]}
    condition: {"metric": "<metric code>", "operator": ">=", "value": 2}

- `AND` — every child must be satisfied; `OR` — at least one child (A5).
- Groups nest to any depth up to MAX_DEPTH.
- A condition may reference only a metric of the approved metric catalog
  (A8, app.achievements.metrics.APPROVED_METRICS). A tree that references
  any other metric is rejected as a whole (`UnsupportedMetricError`) — it
  is never stored as an executable rule and never silently falls back to
  another metric.
- During evaluation a metric whose canonical fact is missing (`None` /
  absent from the facts) never satisfies its condition (§20): missing is
  not "zero" and not "true".

Pure module: no database, no I/O. The same functions validate a rule on
write (app.achievements.service) and evaluate it in the Engine
(app.achievements.engine).
"""

from collections.abc import Callable, Mapping
from typing import Any, Optional

LOGIC_AND = "AND"
LOGIC_OR = "OR"
CANONICAL_LOGIC_OPERATORS: tuple[str, ...] = (LOGIC_AND, LOGIC_OR)

# Comparison operators for an integer metric value.
_COMPARATORS: dict[str, Callable[[int, int], bool]] = {
    ">=": lambda actual, expected: actual >= expected,
    ">": lambda actual, expected: actual > expected,
    "==": lambda actual, expected: actual == expected,
    "<=": lambda actual, expected: actual <= expected,
    "<": lambda actual, expected: actual < expected,
}
CANONICAL_COMPARISON_OPERATORS: tuple[str, ...] = tuple(_COMPARATORS)

MAX_DEPTH = 8
MAX_CHILDREN = 50

_GROUP_KEYS = frozenset({"logic", "conditions"})
_CONDITION_KEYS = frozenset({"metric", "operator", "value"})


class RuleConditionError(ValueError):
    """The condition tree is not a valid structured rule."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


class UnsupportedMetricError(RuleConditionError):
    """A8: the condition references a metric outside the approved catalog."""

    def __init__(self, path: str, metric: str) -> None:
        super().__init__(path, f"metric {metric!r} is not an approved executable metric")
        self.metric = metric


def validate_condition(node: Any, *, approved_metrics: frozenset[str]) -> None:
    """Raise RuleConditionError / UnsupportedMetricError unless `node` is a
    well-formed condition tree whose every metric is approved."""
    _validate(node, approved_metrics=approved_metrics, path="$", depth=1)


def _validate(node: Any, *, approved_metrics: frozenset[str], path: str, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise RuleConditionError(path, f"nesting deeper than {MAX_DEPTH} levels")
    if not isinstance(node, dict):
        raise RuleConditionError(path, "must be an object")
    keys = frozenset(node)
    if "logic" in node or "conditions" in node:
        if keys != _GROUP_KEYS:
            raise RuleConditionError(path, "a group has exactly the keys 'logic' and 'conditions'")
        if node["logic"] not in CANONICAL_LOGIC_OPERATORS:
            raise RuleConditionError(path, "'logic' must be 'AND' or 'OR'")
        children = node["conditions"]
        if not isinstance(children, list) or not children:
            raise RuleConditionError(path, "'conditions' must be a non-empty list")
        if len(children) > MAX_CHILDREN:
            raise RuleConditionError(path, f"a group has at most {MAX_CHILDREN} conditions")
        for index, child in enumerate(children):
            _validate(
                child,
                approved_metrics=approved_metrics,
                path=f"{path}.conditions[{index}]",
                depth=depth + 1,
            )
        return
    if keys != _CONDITION_KEYS:
        raise RuleConditionError(
            path, "a condition has exactly the keys 'metric', 'operator' and 'value'"
        )
    metric = node["metric"]
    if not isinstance(metric, str) or not metric:
        raise RuleConditionError(path, "'metric' must be a non-empty string")
    if metric not in approved_metrics:
        raise UnsupportedMetricError(path, metric)
    if node["operator"] not in _COMPARATORS:
        raise RuleConditionError(
            path, f"'operator' must be one of {', '.join(CANONICAL_COMPARISON_OPERATORS)}"
        )
    value = node["value"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuleConditionError(path, "'value' must be a non-negative integer")


def referenced_metrics(node: Mapping[str, Any]) -> frozenset[str]:
    """Every metric code a (valid) condition tree references."""
    if "conditions" in node:
        result: frozenset[str] = frozenset()
        for child in node["conditions"]:
            result |= referenced_metrics(child)
        return result
    return frozenset({node["metric"]})


def evaluate_condition(node: Mapping[str, Any], facts: Mapping[str, Optional[int]]) -> bool:
    """Evaluate a (valid) condition tree against canonical metric facts.

    A metric missing from `facts`, or present as `None`, does not satisfy
    its condition (§20)."""
    if "conditions" in node:
        results = (evaluate_condition(child, facts) for child in node["conditions"])
        return all(results) if node["logic"] == LOGIC_AND else any(results)
    actual = facts.get(node["metric"])
    if actual is None:
        return False
    return _COMPARATORS[node["operator"]](actual, node["value"])


__all__ = [
    "LOGIC_AND",
    "LOGIC_OR",
    "CANONICAL_LOGIC_OPERATORS",
    "CANONICAL_COMPARISON_OPERATORS",
    "MAX_DEPTH",
    "MAX_CHILDREN",
    "RuleConditionError",
    "UnsupportedMetricError",
    "validate_condition",
    "referenced_metrics",
    "evaluate_condition",
]
