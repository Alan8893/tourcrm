"""Derived EventDocumentRequirement check result for one current-version
Document (TH-0117.4 / Issue #162; ADR-0040 §5, amended by TH-0117.4's own
revoked-mapping resolution; docs/05-api/events-api.md §31).

Pure Python — no FastAPI import, no database session, no ORM import —
mirroring app.people.guardian_lifecycle.effective_status's dependency
direction and shape exactly (a plain function over scalar values, never
an ORM object), so this module can be tested with zero database/HTTP
setup and reused unchanged by any future caller.

Canonical persisted `Document.status` values remain exactly `active`,
`expired`, `revoked` (app.db.documents.CANONICAL_DOCUMENT_STATUSES) — this
module never writes any of them; no background job, scheduler, or
write-time recompute exists or is introduced here.

The requirement-check result this module computes is a **derived**,
narrower vocabulary — exactly `valid`/`expired` for an existing current
Document (never `missing`: that is the absence result the caller
computes one layer up, when no current Document of the required type
exists at all — see app.documents.event_requirements):

- `status = 'revoked'` -> `expired` (ADR-0040 §5's revoked-mapping
  amendment: `revoked` remains its own distinct *persisted* lifecycle
  state — this function never changes it — but the derived check result
  is `expired`, never a fourth `revoked` result value).
- `status = 'expired'` -> `expired` (already explicit).
- `status = 'active'` and `expires_at` has already elapsed -> `expired`
  (read-time derivation, mirroring GuardianRelationship's own
  `effective_status`: the stored column is never rewritten to reflect
  this).
- `status = 'active'` and `expires_at` is `None`, or still in the future
  -> `valid`.
"""

from datetime import datetime, timezone
from typing import Literal, Optional

_TERMINAL_EXPIRED_STATUSES = frozenset({"expired", "revoked"})


def document_check_result(
    *, status: str, expires_at: Optional[datetime], now: Optional[datetime] = None
) -> Literal["valid", "expired"]:
    """`valid` or `expired` for one already-selected current-version
    Document. Never called for an absent Document — the caller decides
    `missing` itself from the absence of any current Document of the
    required type (app.documents.event_requirements).
    """
    if status in _TERMINAL_EXPIRED_STATUSES:
        return "expired"
    current_time = now if now is not None else datetime.now(timezone.utc)
    if expires_at is not None and expires_at <= current_time:
        return "expired"
    return "valid"


__all__ = ["document_check_result"]
