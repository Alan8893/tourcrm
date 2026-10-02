"""Downloadable participant import templates (Issue #228).

Onboarding convenience artifacts for `Люди → Импорт`: a CSV and an XLSX
file whose only content is the header row of canonical import columns —
no sample participants. The columns come from the canonical contract in
app.imports.parsing (`REQUIRED_IMPORT_COLUMNS` + `OPTIONAL_IMPORT_COLUMNS`,
docs/05-api/people-api.md §22 "Source file format"); nothing here defines
or changes import semantics, and the parser/validator stays authoritative.

The files are served as static frontend assets
(`apps/web/public/templates/`). They are generated from this module with
`python -m app.cli.generate_import_templates`, and a unit test fails when
the committed files no longer match the contract.
"""

import csv
import io
from datetime import datetime

from openpyxl import Workbook

from app.imports.parsing import OPTIONAL_IMPORT_COLUMNS, REQUIRED_IMPORT_COLUMNS

# Required columns first, then optional ones, in contract order.
TEMPLATE_COLUMNS: tuple[str, ...] = REQUIRED_IMPORT_COLUMNS + OPTIONAL_IMPORT_COLUMNS

CSV_TEMPLATE_FILENAME = "participant-import-template.csv"
XLSX_TEMPLATE_FILENAME = "participant-import-template.xlsx"

# Fixed document timestamps keep the generated workbook reproducible.
_XLSX_TIMESTAMP = datetime(2026, 1, 1)
_XLSX_SHEET_TITLE = "participants"


def build_csv_template() -> bytes:
    """UTF-8 with a BOM (allowed by the contract; lets spreadsheet apps
    keep Cyrillic values as UTF-8), comma-delimited, header row only."""
    buffer = io.StringIO()
    csv.writer(buffer).writerow(TEMPLATE_COLUMNS)
    return buffer.getvalue().encode("utf-8-sig")


def build_xlsx_template() -> bytes:
    """One worksheet (the only one the importer reads), header row only."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = _XLSX_SHEET_TITLE
    sheet.append(list(TEMPLATE_COLUMNS))
    workbook.properties.creator = "TourCRM"
    workbook.properties.created = _XLSX_TIMESTAMP
    workbook.properties.modified = _XLSX_TIMESTAMP
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


__all__ = [
    "CSV_TEMPLATE_FILENAME",
    "TEMPLATE_COLUMNS",
    "XLSX_TEMPLATE_FILENAME",
    "build_csv_template",
    "build_xlsx_template",
]
