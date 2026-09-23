"""Exact duplicate detection for participant import (TH-0118.2).

Canonical sources: docs/04-modules/people-and-membership.md §11.4,
docs/05-api/people-api.md §22 "Duplicate detection".

Pure Python — the lookup against existing Person/User data is done by
app.imports.queries.find_existing_person_matches and handed in here as
data. Rules, in canonical order:

1. exact `external_id` — only between rows of the current file (no
   persisted external identifier exists to compare against);
2. exact normalized `email`;
3. exact normalized (trimmed) `phone`;
4. exact normalized (trimmed) `first_name` + `last_name` + `birth_date`.

Fuzzy matching is deferred (no canonical algorithm/fields/threshold) and
is not performed.

Every duplicate is a `duplicate_exact` **warning** — never an error: the
row stays valid, and nothing is merged, updated, overwritten or reused.
For one counterpart only the first matching rule (canonical order) is
reported, so the same pair is never reported twice. Inside the file,
every conflicting row is marked (row 10 and row 25, not only row 25),
with `matched_person_id = NULL`; a match against an existing Person
carries that Person's id and nothing else about them.
"""

import uuid
from collections import defaultdict
from typing import Hashable, Sequence

from app.imports.rows import SEVERITY_WARNING, ImportIssue, NormalizedRow

DUPLICATE_EXACT_CODE = "duplicate_exact"

# Canonical order (people-and-membership.md §11.4).
DUPLICATE_RULES: tuple[str, ...] = ("external_id", "email", "phone", "name_birth_date")

# Only so many counterpart row numbers are spelled out in one message.
_MAX_LISTED_ROWS = 20

_RULE_FIELD: dict[str, str | None] = {
    "external_id": "external_id",
    "email": "email",
    "phone": "phone",
    # Composite key — not one column.
    "name_birth_date": None,
}
_RULE_LABEL: dict[str, str] = {
    "external_id": "external_id",
    "email": "email",
    "phone": "phone",
    "name_birth_date": "first_name + last_name + birth_date",
}


def _list_rows(row_numbers: set[int]) -> str:
    ordered = sorted(row_numbers)
    listed = ", ".join(str(number) for number in ordered[:_MAX_LISTED_ROWS])
    if len(ordered) > _MAX_LISTED_ROWS:
        listed += f" and {len(ordered) - _MAX_LISTED_ROWS} more"
    return listed


def rule_key(row: NormalizedRow, rule: str) -> Hashable | None:
    """The row's match key for `rule`, or None when the row has no usable
    (present and valid) value for it."""
    if rule == "external_id":
        return row.external_id
    if rule == "email":
        return row.email
    if rule == "phone":
        return row.phone
    if rule == "name_birth_date":
        if row.first_name and row.last_name and row.birth_date:
            return (row.first_name, row.last_name, row.birth_date)
        return None
    raise ValueError(f"Unknown duplicate rule: {rule!r}")


def detect_duplicates(
    rows: Sequence[NormalizedRow],
    existing_matches: dict[int, list[tuple[uuid.UUID, str]]],
) -> list[ImportIssue]:
    """`existing_matches` maps a row_number to the (person_id, rule) pairs
    under which it matched an existing Person/User."""
    issues: list[ImportIssue] = []

    # For each row: the counterpart rows per rule, where a counterpart is
    # attributed only to the first rule (canonical order) that pairs them.
    counterparts: dict[int, dict[str, set[int]]] = defaultdict(dict)
    already_paired: dict[int, set[int]] = defaultdict(set)
    for rule in DUPLICATE_RULES:
        groups: dict[Hashable, list[int]] = defaultdict(list)
        for row in rows:
            key = rule_key(row, rule)
            if key is not None:
                groups[key].append(row.row_number)
        for members in groups.values():
            if len(members) < 2:
                continue
            member_set = set(members)
            for row_number in members:
                others = member_set - {row_number} - already_paired[row_number]
                if others:
                    counterparts[row_number][rule] = others
                    already_paired[row_number] |= others

    for row in rows:
        by_rule = counterparts.get(row.row_number, {})
        for rule in DUPLICATE_RULES:
            paired = by_rule.get(rule)
            if paired:
                issues.append(
                    ImportIssue(
                        code=DUPLICATE_EXACT_CODE,
                        message=(
                            f"Exact duplicate of row(s) {_list_rows(paired)} in this file "
                            f"by {_RULE_LABEL[rule]}"
                        ),
                        severity=SEVERITY_WARNING,
                        row_number=row.row_number,
                        field=_RULE_FIELD[rule],
                    )
                )

        first_rule_for_person: dict[uuid.UUID, str] = {}
        for person_id, rule in existing_matches.get(row.row_number, []):
            current = first_rule_for_person.get(person_id)
            if current is None or DUPLICATE_RULES.index(rule) < DUPLICATE_RULES.index(current):
                first_rule_for_person[person_id] = rule
        for person_id, rule in sorted(
            first_rule_for_person.items(),
            key=lambda item: (DUPLICATE_RULES.index(item[1]), str(item[0])),
        ):
            issues.append(
                ImportIssue(
                    code=DUPLICATE_EXACT_CODE,
                    message=f"Exact duplicate of an existing person by {_RULE_LABEL[rule]}",
                    severity=SEVERITY_WARNING,
                    row_number=row.row_number,
                    field=_RULE_FIELD[rule],
                    matched_person_id=person_id,
                )
            )
    return issues


__all__ = [
    "DUPLICATE_EXACT_CODE",
    "DUPLICATE_RULES",
    "rule_key",
    "detect_duplicates",
]
