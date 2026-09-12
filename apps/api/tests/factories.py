"""Technical-only test factories (Issue #7).

These produce opaque, synthetic technical values with no business meaning —
no Person/User/Trip/Finance data. They exist so unit/integration/API/
authorization tests can obtain a fresh identifier or placeholder string
without depending on any domain model.
"""

import uuid


def unique_id() -> str:
    """A fresh opaque identifier, e.g. for a technical probe row or a
    request-id test value. Carries no business meaning.
    """
    return str(uuid.uuid4())


def unique_token(prefix: str = "test") -> str:
    """A short, synthetic, non-secret opaque string for placeholder use in
    tests (e.g. a fake header value). Never a real credential.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
