"""Pure unit tests for TH-0118.2 row normalization/validation
(app.imports.rows) — no database, no HTTP."""

import datetime

import pytest

from app.imports.parsing import SourceRecord
from app.imports.rows import normalize_and_validate


def _row(source_format: str = "csv", **values: object):
    record = SourceRecord(
        row_number=7, values={"first_name": "Anna", "last_name": "Ivanova", **values}
    )
    return normalize_and_validate(record, source_format=source_format)


def _codes(issues) -> list[tuple[str | None, str]]:
    return [(issue.field, issue.code) for issue in issues]


# --- normalization ------------------------------------------------------------


def test_valid_row_with_every_field() -> None:
    row, issues = _row(
        middle_name="Petrovna",
        birth_date="2010-05-01",
        phone="+7 900 123-45-67",
        email="anna@example.com",
        external_id="EXT-1",
    )

    assert issues == []
    assert row.row_number == 7
    assert (row.first_name, row.last_name, row.middle_name) == ("Anna", "Ivanova", "Petrovna")
    assert row.birth_date == datetime.date(2010, 5, 1)
    assert row.phone == "+7 900 123-45-67"
    assert row.email == "anna@example.com"
    assert row.external_id == "EXT-1"


def test_whitespace_is_trimmed() -> None:
    row, issues = _row(
        first_name="  Anna ",
        last_name="\tIvanova\n",
        middle_name=" P ",
        birth_date=" 2010-05-01 ",
        external_id=" X1 ",
    )
    assert issues == []
    assert (row.first_name, row.last_name, row.middle_name) == ("Anna", "Ivanova", "P")
    assert row.birth_date == datetime.date(2010, 5, 1)
    assert row.external_id == "X1"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_optional_values_become_null(blank) -> None:
    row, issues = _row(
        middle_name=blank, birth_date=blank, phone=blank, email=blank, external_id=blank
    )
    assert issues == []
    assert (row.middle_name, row.birth_date, row.phone, row.email, row.external_id) == (
        None,
        None,
        None,
        None,
        None,
    )


def test_missing_email_is_not_an_error() -> None:
    row, issues = _row()
    assert issues == []
    assert row.email is None


def test_email_uses_the_canonical_login_identifier_normalization() -> None:
    row, issues = _row(email="  Anna.Ivanova@Example.COM ")
    assert issues == []
    assert row.email == "anna.ivanova@example.com"


def test_phone_is_only_trimmed() -> None:
    row, _ = _row(phone="  8 (900) 123-45-67  ")
    assert row.phone == "8 (900) 123-45-67"


def test_xlsx_number_cells_become_plain_text() -> None:
    row, issues = _row("xlsx", phone=79001234567, external_id=42.0)
    assert issues == []
    assert row.phone == "79001234567"
    assert row.external_id == "42"


# --- birth_date ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["01.05.2010", "05/01/2010", "2010/05/01", "2010-5-1", "20100501", "2010-02-30"]
)
def test_csv_birth_date_accepts_only_valid_iso_dates(value: str) -> None:
    row, issues = _row(birth_date=value)
    assert _codes(issues) == [("birth_date", "invalid_birth_date")]
    assert row.birth_date is None


@pytest.mark.parametrize(
    "value,expected",
    [
        (datetime.datetime(2010, 5, 1, 0, 0), datetime.date(2010, 5, 1)),
        (datetime.date(2011, 6, 2), datetime.date(2011, 6, 2)),
    ],
)
def test_xlsx_birth_date_accepts_native_date_cells(value, expected) -> None:
    row, issues = _row("xlsx", birth_date=value)
    assert issues == []
    assert row.birth_date == expected


@pytest.mark.parametrize("value", ["2010-05-01", "01.05.2010", 40299, 40299.0])
def test_xlsx_birth_date_rejects_non_date_cells(value) -> None:
    _, issues = _row("xlsx", birth_date=value)
    assert _codes(issues) == [("birth_date", "invalid_birth_date")]


# --- validation ---------------------------------------------------------------


@pytest.mark.parametrize("field", ["first_name", "last_name"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_required_fields(field: str, value) -> None:
    row, issues = _row(**{field: value})
    assert _codes(issues) == [(field, "required_field_missing")]
    assert all(issue.severity == "error" for issue in issues)
    assert getattr(row, field) is None


@pytest.mark.parametrize(
    "value", ["anna", "anna@", "@example.com", "an na@example.com", "a@b@c", "anna@exa mple"]
)
def test_invalid_email(value: str) -> None:
    row, issues = _row(email=value)
    assert _codes(issues) == [("email", "invalid_email")]
    assert row.email is None


def test_email_longer_than_255_is_rejected() -> None:
    row, issues = _row(email="a" * 250 + "@example.com")
    assert _codes(issues) == [("email", "value_too_long")]
    assert row.email is None


def test_phone_longer_than_person_contract_is_rejected() -> None:
    row, issues = _row(phone="1" * 33)
    assert _codes(issues) == [("phone", "value_too_long")]
    assert row.phone is None
    _, issues = _row(phone="1" * 32)
    assert issues == []


@pytest.mark.parametrize("field", ["first_name", "last_name", "middle_name"])
def test_names_longer_than_person_contract_are_rejected(field: str) -> None:
    _, issues = _row(**{field: "x" * 256})
    assert _codes(issues) == [(field, "value_too_long")]


def test_non_text_xlsx_cell_in_text_field_is_rejected() -> None:
    _, issues = _row("xlsx", first_name=True, email=datetime.datetime(2020, 1, 1))
    assert sorted(_codes(issues)) == [
        ("email", "invalid_value_type"),
        ("first_name", "invalid_value_type"),
    ]


def test_multiple_errors_in_one_row_are_all_reported() -> None:
    _, issues = _row(first_name="", last_name=None, email="not-an-email", birth_date="1.1.2000")

    assert sorted(_codes(issues)) == [
        ("birth_date", "invalid_birth_date"),
        ("email", "invalid_email"),
        ("first_name", "required_field_missing"),
        ("last_name", "required_field_missing"),
    ]
    assert {issue.row_number for issue in issues} == {7}
    assert {issue.severity for issue in issues} == {"error"}


def test_messages_never_echo_cell_values() -> None:
    _, issues = _row(email="secret-value", birth_date="secret-date")
    for issue in issues:
        assert "secret" not in issue.message
