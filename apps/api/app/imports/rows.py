"""Row normalization and validation for participant import (TH-0118.2).

Canonical sources: docs/05-api/people-api.md §22 "Row normalization and
validation", docs/04-modules/people-and-membership.md §11.

Pure Python — no FastAPI, no database session. Normalization runs first,
then validation of the normalized values:

- text values are trimmed; a blank value becomes `None`;
- `email` goes through the project-wide `normalize_login_identifier()`
  (trim + lowercase) — no second email normalizer exists;
- `phone` is only trimmed: TourCRM has no canonical phone normalization
  (Person.phone is free text of at most 32 characters);
- `birth_date` becomes a `datetime.date`: a CSV value must be exactly
  `YYYY-MM-DD`; an XLSX value must be a native date/datetime cell. No other
  format is guessed.

Validation (each failure is an `error`, several per row are allowed):
`first_name`/`last_name` are required; text lengths follow the Person
contract (names/email 255, phone 32); `email` must have the minimal
`local@domain` shape; `birth_date` must be valid per the rules above. A
missing email is not an error. Issue messages never echo cell values.
"""

import datetime
import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.db.identity import normalize_login_identifier
from app.imports.parsing import SourceRecord

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

# Person contract column lengths (app.db.identity.Person / persons_schemas).
_MAX_LENGTHS: dict[str, int] = {
    "first_name": 255,
    "last_name": 255,
    "middle_name": 255,
    "phone": 32,
    "email": 255,
}
_TEXT_FIELDS = ("first_name", "last_name", "middle_name", "phone", "email", "external_id")
_REQUIRED_FIELDS = ("first_name", "last_name")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MINIMAL_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")


@dataclass(frozen=True)
class ImportIssue:
    """One error or warning about the source file or one of its rows —
    persisted as an `ImportJobError` row."""

    code: str
    message: str
    severity: str
    row_number: int | None = None
    field: str | None = None
    matched_person_id: uuid.UUID | None = None


@dataclass(frozen=True)
class NormalizedRow:
    """A record's normalized values. A field that failed validation is
    `None` here, so later stages (duplicate detection) never use it."""

    row_number: int
    first_name: str | None
    last_name: str | None
    middle_name: str | None
    birth_date: datetime.date | None
    phone: str | None
    email: str | None
    external_id: str | None


class _InvalidType(Exception):
    pass


def _as_text(value: Any) -> str | None:
    """Trim a text cell; blank -> None. XLSX number cells (e.g. a phone or
    external_id typed as a number) become their plain decimal text; any
    other non-text cell (date, boolean, ...) is not a text value."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise _InvalidType
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = str(int(value)) if value.is_integer() else str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise _InvalidType
    text = text.strip()
    return text or None


def _parse_birth_date(value: Any, source_format: str) -> datetime.date:
    if source_format == "xlsx":
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        raise ValueError("not a native date cell")
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        raise ValueError("not YYYY-MM-DD")
    return datetime.date.fromisoformat(value)


def normalize_and_validate(
    record: SourceRecord, *, source_format: str
) -> tuple[NormalizedRow, list[ImportIssue]]:
    row_number = record.row_number
    issues: list[ImportIssue] = []

    def error(field: str, code: str, message: str) -> None:
        issues.append(
            ImportIssue(
                code=code,
                message=message,
                severity=SEVERITY_ERROR,
                row_number=row_number,
                field=field,
            )
        )

    values: dict[str, str | None] = {}
    for field in _TEXT_FIELDS:
        try:
            text = _as_text(record.values.get(field))
        except _InvalidType:
            error(field, "invalid_value_type", f"`{field}` must be a text value")
            values[field] = None
            continue
        if field == "email" and text is not None:
            text = normalize_login_identifier(text)
        max_length = _MAX_LENGTHS.get(field)
        if text is not None and max_length is not None and len(text) > max_length:
            error(field, "value_too_long", f"`{field}` is longer than {max_length} characters")
            text = None
        values[field] = text

    for field in _REQUIRED_FIELDS:
        if values[field] is None and not any(issue.field == field for issue in issues):
            error(field, "required_field_missing", f"`{field}` is required")

    if values["email"] is not None and not _MINIMAL_EMAIL.match(values["email"]):
        error("email", "invalid_email", "`email` must have the form local@domain")
        values["email"] = None

    birth_date: datetime.date | None = None
    raw_birth_date = record.values.get("birth_date")
    if isinstance(raw_birth_date, str):
        raw_birth_date = raw_birth_date.strip() or None
    if raw_birth_date is not None:
        try:
            birth_date = _parse_birth_date(raw_birth_date, source_format)
        except ValueError:
            expected = "a date cell" if source_format == "xlsx" else "a YYYY-MM-DD date"
            error("birth_date", "invalid_birth_date", f"`birth_date` must be {expected}")

    row = NormalizedRow(
        row_number=row_number,
        first_name=values["first_name"],
        last_name=values["last_name"],
        middle_name=values["middle_name"],
        birth_date=birth_date,
        phone=values["phone"],
        email=values["email"],
        external_id=values["external_id"],
    )
    return row, issues


__all__ = [
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "ImportIssue",
    "NormalizedRow",
    "normalize_and_validate",
]
