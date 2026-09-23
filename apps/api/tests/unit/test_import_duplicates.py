"""Pure unit tests for TH-0118.2 exact duplicate detection
(app.imports.duplicates) — no database, no HTTP. Existing-Person matches
are handed in as data, exactly as app.imports.queries produces them."""

import datetime
import uuid

from app.imports.duplicates import detect_duplicates
from app.imports.rows import NormalizedRow


def _row(row_number: int, **overrides) -> NormalizedRow:
    values = {
        "first_name": f"First{row_number}",
        "last_name": f"Last{row_number}",
        "middle_name": None,
        "birth_date": None,
        "phone": None,
        "email": None,
        "external_id": None,
    }
    values.update(overrides)
    return NormalizedRow(row_number=row_number, **values)


def _summary(issues) -> list[tuple[int | None, str | None, str, uuid.UUID | None]]:
    return [
        (issue.row_number, issue.field, issue.code, issue.matched_person_id) for issue in issues
    ]


def test_no_duplicates_no_issues() -> None:
    rows = [_row(2, email="a@x.io"), _row(3, email="b@x.io")]
    assert detect_duplicates(rows, {}) == []


def test_in_file_external_id_duplicate_marks_every_conflicting_row() -> None:
    rows = [_row(10, external_id="E1"), _row(11, external_id="E2"), _row(25, external_id="E1")]

    issues = detect_duplicates(rows, {})

    assert _summary(issues) == [
        (10, "external_id", "duplicate_exact", None),
        (25, "external_id", "duplicate_exact", None),
    ]
    assert all(issue.severity == "warning" for issue in issues)
    assert "25" in issues[0].message and "10" in issues[1].message


def test_in_file_email_phone_and_name_birth_date_duplicates() -> None:
    birth = datetime.date(2010, 5, 1)
    rows = [
        _row(2, email="a@x.io"),
        _row(3, email="a@x.io"),
        _row(4, phone="+7 900"),
        _row(5, phone="+7 900"),
        _row(6, first_name="Anna", last_name="Ivanova", birth_date=birth),
        _row(7, first_name="Anna", last_name="Ivanova", birth_date=birth),
    ]

    assert _summary(detect_duplicates(rows, {})) == [
        (2, "email", "duplicate_exact", None),
        (3, "email", "duplicate_exact", None),
        (4, "phone", "duplicate_exact", None),
        (5, "phone", "duplicate_exact", None),
        (6, None, "duplicate_exact", None),
        (7, None, "duplicate_exact", None),
    ]


def test_three_way_in_file_duplicate_lists_all_counterparts() -> None:
    rows = [_row(10, email="a@x.io"), _row(25, email="a@x.io"), _row(40, email="a@x.io")]

    issues = detect_duplicates(rows, {})

    assert [issue.row_number for issue in issues] == [10, 25, 40]
    assert "25, 40" in issues[0].message


def test_same_pair_is_reported_once_under_the_first_rule_in_canonical_order() -> None:
    rows = [
        _row(2, external_id="E1", email="a@x.io", phone="1"),
        _row(3, external_id="E1", email="a@x.io", phone="1"),
    ]

    assert _summary(detect_duplicates(rows, {})) == [
        (2, "external_id", "duplicate_exact", None),
        (3, "external_id", "duplicate_exact", None),
    ]


def test_name_birth_date_needs_all_three_values() -> None:
    rows = [
        _row(2, first_name="Anna", last_name="Ivanova", birth_date=None),
        _row(3, first_name="Anna", last_name="Ivanova", birth_date=None),
    ]
    assert detect_duplicates(rows, {}) == []


def test_fuzzy_matching_is_not_performed() -> None:
    birth = datetime.date(2010, 5, 1)
    rows = [
        _row(2, first_name="Anna", last_name="Ivanova", birth_date=birth),
        _row(3, first_name="Ana", last_name="Ivanova", birth_date=birth),
        _row(4, first_name="anna", last_name="ivanova", birth_date=birth),
        _row(5, first_name="Anna", last_name="Ivanova", birth_date=None),
        _row(6, email="anna@x.io"),
        _row(7, email="anna@x.com"),
    ]
    assert detect_duplicates(rows, {}) == []


def test_existing_person_match_carries_only_the_person_id() -> None:
    person_id = uuid.uuid4()
    rows = [_row(2, email="a@x.io"), _row(3)]

    issues = detect_duplicates(rows, {2: [(person_id, "email")]})

    assert _summary(issues) == [(2, "email", "duplicate_exact", person_id)]
    assert issues[0].severity == "warning"
    assert "existing person" in issues[0].message


def test_existing_person_matched_by_several_rules_is_reported_once_by_the_first() -> None:
    person_id = uuid.uuid4()
    rows = [_row(2, email="a@x.io", phone="1")]

    issues = detect_duplicates(
        rows,
        {2: [(person_id, "name_birth_date"), (person_id, "phone"), (person_id, "email")]},
    )

    assert _summary(issues) == [(2, "email", "duplicate_exact", person_id)]


def test_several_existing_persons_and_in_file_duplicate_are_all_reported() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    rows = [_row(2, email="a@x.io", phone="1"), _row(3, email="a@x.io")]

    issues = detect_duplicates(rows, {2: [(first, "email"), (second, "phone")]})

    assert _summary(issues) == [
        (2, "email", "duplicate_exact", None),
        (2, "email", "duplicate_exact", first),
        (2, "phone", "duplicate_exact", second),
        (3, "email", "duplicate_exact", None),
    ]


def test_large_in_file_group_lists_a_bounded_number_of_rows() -> None:
    rows = [_row(number, phone="same") for number in range(2, 52)]

    issues = detect_duplicates(rows, {})

    assert len(issues) == 50
    assert issues[0].message.startswith("Exact duplicate of row(s) 3, 4,")
    assert "and 29 more" in issues[0].message
