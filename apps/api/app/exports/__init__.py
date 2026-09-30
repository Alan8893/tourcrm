"""Participant Export (TH-0118.4 / Issue #218).

Canonical contract: docs/05-api/participant-export-api.md, with the PO
decisions on GAP-1..GAP-7 recorded on Issue #218.

- `fields` — the backend-authoritative canonical export-field allowlist;
- `authorization` — the one authorization policy every output format goes
  through (Administrator in the current Club + existing read grants);
- `queries` — canonical dataset construction (Club/Group/Event/Group+Event);
- `service` — the single entry point: validate, authorize, build dataset;
- `rendering` — presentation only (XLSX, PDF, print HTML) over that dataset.
"""
