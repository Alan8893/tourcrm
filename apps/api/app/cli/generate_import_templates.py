"""Writes the downloadable participant import templates (Issue #228).

Invocation (from apps/api, in a repository checkout):

    python -m app.cli.generate_import_templates

Regenerates `apps/web/public/templates/participant-import-template.csv`
and `.xlsx` from the canonical import columns (app.imports.templates).
Run it whenever the canonical columns change;
tests/unit/test_import_templates.py fails until the files are updated.
"""

import sys
from pathlib import Path

from app.imports.templates import (
    CSV_TEMPLATE_FILENAME,
    XLSX_TEMPLATE_FILENAME,
    build_csv_template,
    build_xlsx_template,
)

# apps/api/app/cli/ -> apps/web/public/templates/
TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "web" / "public" / "templates"


def main() -> int:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    for filename, content in (
        (CSV_TEMPLATE_FILENAME, build_csv_template()),
        (XLSX_TEMPLATE_FILENAME, build_xlsx_template()),
    ):
        path = TEMPLATES_DIR / filename
        path.write_bytes(content)
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
