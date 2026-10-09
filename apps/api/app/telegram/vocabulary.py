"""Canonical Telegram integration vocabularies (Issue #329, ADR-0047).

Pure Python — no ORM/FastAPI import, mirroring app.notifications.vocabulary:
app.db.telegram builds its CHECK constraints from these sets and the
linking service uses the same literals.
"""

from datetime import timedelta

# `telegram_identities.status` (ADR-0047 §4.2: unlink/revoke is a lifecycle
# transition, never a destructive delete):
# - active   — the User's current linked Telegram identity;
# - unlinked — ended by the User's explicit unlink;
# - replaced — ended because the same User linked a different Telegram
#              account through a new one-time challenge.
IDENTITY_ACTIVE = "active"
IDENTITY_UNLINKED = "unlinked"
IDENTITY_REPLACED = "replaced"

CANONICAL_IDENTITY_STATUSES: frozenset[str] = frozenset(
    {IDENTITY_ACTIVE, IDENTITY_UNLINKED, IDENTITY_REPLACED}
)

# `telegram_link_challenges.status`. Expiry is derived from `expires_at`
# against authoritative database time, not a stored status.
CHALLENGE_PENDING = "pending"
CHALLENGE_CONSUMED = "consumed"
CHALLENGE_REVOKED = "revoked"

CANONICAL_CHALLENGE_STATUSES: frozenset[str] = frozenset(
    {CHALLENGE_PENDING, CHALLENGE_CONSUMED, CHALLENGE_REVOKED}
)

# Lifetime of one linking challenge, and the per-User issuance rate limit
# (ADR-0047 §4.3 "bounded expiry and rate-limit challenge creation/
# reissue"): at most CHALLENGE_RATE_LIMIT_COUNT challenges per User in any
# rolling CHALLENGE_RATE_LIMIT_WINDOW.
CHALLENGE_TTL = timedelta(minutes=15)
CHALLENGE_RATE_LIMIT_COUNT = 5
CHALLENGE_RATE_LIMIT_WINDOW = timedelta(hours=1)

# Telegram deep-link `start` parameter: 1-64 characters of A-Z, a-z, 0-9,
# `_` and `-` (Bot API "Deep linking"). The raw challenge token
# (app.authentication.tokens.generate_token, 43 URL-safe base64 characters)
# fits this alphabet and length.
START_PARAMETER_PATTERN = r"[A-Za-z0-9_-]{1,64}"


__all__ = [
    "IDENTITY_ACTIVE",
    "IDENTITY_UNLINKED",
    "IDENTITY_REPLACED",
    "CANONICAL_IDENTITY_STATUSES",
    "CHALLENGE_PENDING",
    "CHALLENGE_CONSUMED",
    "CHALLENGE_REVOKED",
    "CANONICAL_CHALLENGE_STATUSES",
    "CHALLENGE_TTL",
    "CHALLENGE_RATE_LIMIT_COUNT",
    "CHALLENGE_RATE_LIMIT_WINDOW",
    "START_PARAMETER_PATTERN",
]
