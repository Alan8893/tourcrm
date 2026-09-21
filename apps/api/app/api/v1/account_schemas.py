"""Request/response models for Person-scoped account management
(`/api/v1/persons/{person_id}/account*`, TH-0113 / ADR-0038).

Single-resource responses are returned directly per ADR-0014, except the
one deliberate exception ADR-0038 §3 itself carries: the create/reset
response also carries the one-time raw temporary credential, since that
is the single specifically-permitted delivery point for it (never
`PersonAccountOut` alone, never any other endpoint).

`PersonAccountOut` is the safe projection of `User` — it never includes
`password_hash`, any token/hash, or any other field
`docs/07-security/security-and-privacy.md` §2.6 forbids from ever
appearing in an ordinary API response.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class PersonAccountOut(BaseModel):
    id: UUID
    person_id: UUID
    # TH-0116: NULL for a pending-stub account (a Person with no email
    # yet) — see app.authentication.account_provisioning's module
    # docstring. Never a placeholder/fake identifier.
    login_identifier: Optional[str]
    status: str
    email_verified_at: Optional[datetime]
    last_login_at: Optional[datetime]


class PersonAccountCredentialOut(BaseModel):
    """Response for `POST .../account` (create) and `POST .../account/
    password-reset` (admin reset) — the only two responses in this API
    that ever carry a raw credential, per ADR-0038 §3/§8. `temporary_
    credential` is the one-time setup/reset secret itself: it exists only
    in this one response body, is never persisted anywhere after being
    generated (app.authentication.tokens.hash_token stores only its
    hash), and the server has no way to recover it once this response has
    been sent.

    TH-0116: `temporary_credential` is `None` for exactly one outcome of
    `POST .../account` — creating a pending-stub account for a Person
    with no email, where there is no identifier to issue a challenge to.
    `POST .../account/password-reset` never returns `None` here (it
    rejects a pending-stub account outright — see
    `admin_reset_password_for_person`).
    """

    account: PersonAccountOut
    temporary_credential: Optional[str] = None
