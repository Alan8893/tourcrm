"""Participant import job domain (TH-0118.1 / Issue #185).

Canonical sources: docs/05-api/people-api.md §22,
docs/04-modules/people-and-membership.md §11.

Foundation only: ImportJob lifecycle (`app.imports.lifecycle`), the
ImportJob object-access policy (`app.imports.authorization`), job creation
and lifecycle transitions (`app.imports.service`) and the error read path
(`app.imports.queries`). No parse/validate/preview/approve/apply stage is
implemented here — creating a job never reads, parses or applies the
uploaded file's content.
"""
