"""Pure unit tests for TH-0118.2 source-file parsing (app.imports.parsing)
— no database, no HTTP. XLSX inputs are built in memory with openpyxl
(the production parser itself only ever reads)."""

import datetime
import io

import pytest
from openpyxl import Workbook

from app.imports.parsing import ImportParseError, parse_csv, parse_source, parse_xlsx


def _xlsx(*sheets: list[list[object]]) -> bytes:
    workbook = Workbook()
    first = workbook.active
    for index, rows in enumerate(sheets):
        sheet = first if index == 0 else workbook.create_sheet(f"Sheet{index + 1}")
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _parse_error(func, content: bytes) -> ImportParseError:
    with pytest.raises(ImportParseError) as info:
        func(content)
    return info.value


# --- CSV --------------------------------------------------------------------


def test_csv_valid_file() -> None:
    parsed = parse_csv(
        b"first_name,last_name,email\nAnna,Ivanova,anna@example.com\nBoris,Petrov,\n"
    )

    assert parsed.columns == ("first_name", "last_name", "email")
    assert [record.row_number for record in parsed.records] == [2, 3]
    assert parsed.records[0].values == {
        "first_name": "Anna",
        "last_name": "Ivanova",
        "email": "anna@example.com",
    }
    assert parsed.records[1].values["email"] == ""


def test_csv_utf8_bom_and_quoting_and_header_whitespace() -> None:
    content = '\ufeff first_name ,last_name\n"Анна, мл.",Иванова\n'.encode("utf-8")

    parsed = parse_csv(content)

    assert parsed.columns == ("first_name", "last_name")
    assert parsed.records[0].values == {"first_name": "Анна, мл.", "last_name": "Иванова"}


def test_csv_all_canonical_columns_are_accepted() -> None:
    header = "first_name,last_name,middle_name,birth_date,phone,email,external_id"
    parsed = parse_csv(f"{header}\n".encode())
    assert set(parsed.columns) == set(header.split(","))
    assert parsed.records == ()


@pytest.mark.parametrize("content", [b"", b"\n", b" , \n", b",,,\nAnna,Ivanova\n"])
def test_csv_missing_header(content: bytes) -> None:
    error = _parse_error(parse_csv, content)
    assert error.code == "import_header_missing"


def test_csv_header_without_required_column() -> None:
    error = _parse_error(parse_csv, b"first_name,email\nAnna,a@b.c\n")
    assert error.code == "import_header_missing_required_column"
    assert error.field == "last_name"
    assert error.row_number == 1


@pytest.mark.parametrize("column", ["FirstName", "First_Name", "name", "surname", "role"])
def test_csv_unknown_column_is_a_header_error(column: str) -> None:
    error = _parse_error(parse_csv, f"first_name,last_name,{column}\n".encode())
    assert error.code == "import_header_unknown_column"
    assert error.field == column


def test_csv_blank_column_between_named_columns_is_a_header_error() -> None:
    error = _parse_error(parse_csv, b"first_name,,last_name\n")
    assert error.code == "import_header_unknown_column"
    assert error.field is None


def test_csv_duplicate_column_is_a_header_error() -> None:
    error = _parse_error(parse_csv, b"first_name,last_name,email,email\n")
    assert error.code == "import_header_duplicate_column"
    assert error.field == "email"


@pytest.mark.parametrize(
    "content",
    [
        b'first_name,last_name\n"Anna,Ivanova\n',  # unterminated quote
        b'first_name,last_name\n"Anna"x,Ivanova\n',  # text after a closing quote
        b"first_name,last_name\nAn\x00na,Ivanova\n",  # NUL byte
    ],
)
def test_csv_malformed(content: bytes) -> None:
    assert _parse_error(parse_csv, content).code == "import_file_malformed"


def test_csv_value_outside_header_columns_is_malformed() -> None:
    error = _parse_error(parse_csv, b"first_name,last_name\nAnna,Ivanova,extra\n")
    assert error.code == "import_file_malformed"
    assert error.row_number == 2


def test_csv_not_utf8_is_unreadable() -> None:
    content = "first_name,last_name\nАнна,Иванова\n".encode("cp1251")
    assert _parse_error(parse_csv, content).code == "import_file_unreadable"


def test_csv_empty_rows_are_skipped_and_row_numbers_follow_the_file() -> None:
    parsed = parse_csv(b"first_name,last_name\n\nAnna,Ivanova\n , \n,,\nBoris,Petrov\n")

    assert [record.row_number for record in parsed.records] == [3, 6]


def test_csv_short_row_leaves_missing_columns_empty() -> None:
    parsed = parse_csv(b"first_name,last_name,email\nAnna,Ivanova\n")
    assert parsed.records[0].values["email"] is None


def test_csv_header_only_file_has_no_records() -> None:
    assert parse_csv(b"first_name,last_name\n").records == ()


# --- XLSX -------------------------------------------------------------------


def test_xlsx_valid_first_worksheet_with_native_values() -> None:
    content = _xlsx(
        [
            ["first_name", "last_name", "birth_date", "phone"],
            ["Anna", "Ivanova", datetime.date(2010, 5, 1), 79001234567],
        ]
    )

    parsed = parse_xlsx(content)

    assert parsed.columns == ("first_name", "last_name", "birth_date", "phone")
    record = parsed.records[0]
    assert record.row_number == 2
    assert record.values["first_name"] == "Anna"
    assert record.values["birth_date"] == datetime.datetime(2010, 5, 1)
    assert record.values["phone"] == 79001234567


def test_xlsx_only_the_first_worksheet_is_read() -> None:
    content = _xlsx(
        [["first_name", "last_name"], ["Anna", "Ivanova"]],
        [["first_name", "last_name"], ["Second", "Sheet"], ["Also", "Ignored"]],
    )

    parsed = parse_xlsx(content)

    assert [record.values["first_name"] for record in parsed.records] == ["Anna"]


def test_xlsx_first_worksheet_is_used_even_if_a_later_one_has_a_bad_header() -> None:
    content = _xlsx([["first_name", "last_name"], ["Anna", "Ivanova"]], [["unknown"]])
    assert len(parse_xlsx(content).records) == 1


def test_xlsx_empty_first_worksheet_fails_even_if_a_later_one_has_data() -> None:
    content = _xlsx([], [["first_name", "last_name"], ["Anna", "Ivanova"]])
    assert _parse_error(parse_xlsx, content).code == "import_header_missing"


def test_xlsx_invalid_header() -> None:
    content = _xlsx([["first_name", "surname"], ["Anna", "Ivanova"]])
    error = _parse_error(parse_xlsx, content)
    assert error.code == "import_header_unknown_column"
    assert error.field == "surname"


def test_xlsx_duplicate_header_column() -> None:
    content = _xlsx([["first_name", "last_name", "last_name"]])
    assert _parse_error(parse_xlsx, content).code == "import_header_duplicate_column"


@pytest.mark.parametrize(
    "content",
    [b"", b"not a zip file", b"PK\x03\x04truncated", b"first_name,last_name\nAnna,Ivanova\n"],
)
def test_xlsx_malformed_or_unreadable(content: bytes) -> None:
    assert _parse_error(parse_xlsx, content).code == "import_file_unreadable"


def test_xlsx_empty_rows_are_skipped() -> None:
    content = _xlsx([["first_name", "last_name"], [None, None], ["Anna", "Ivanova"], ["  ", None]])
    assert [record.row_number for record in parse_xlsx(content).records] == [3]


def test_parse_source_dispatches_on_format() -> None:
    csv_content = b"first_name,last_name\nAnna,Ivanova\n"
    assert len(parse_source(csv_content, "csv").records) == 1
    xlsx_content = _xlsx([["first_name", "last_name"], ["Anna", "Ivanova"]])
    assert len(parse_source(xlsx_content, "xlsx").records) == 1
