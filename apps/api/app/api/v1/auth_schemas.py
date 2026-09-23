"""Request/response models for /api/v1/auth (Issue #33).

Single-resource responses are returned directly per ADR-0014 (no `data`
wrapper) — these are that direct payload shape, not a second envelope.
None of these models ever carries a password, a raw session/challenge
token, or a password hash (auth-api.md §3: "Пароли никогда не
возвращаются API").
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=256)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    middle_name: str | None = Field(default=None, max_length=255)
    birth_date: date | None = None


class PersonOut(BaseModel):
    # TH-0119: `id`/`photo_file_id` let the authenticated UI build the
    # versioned profile photo URL; no storage internals are exposed.
    id: UUID
    first_name: str
    last_name: str
    middle_name: str | None
    birth_date: date | None
    photo_file_id: UUID | None


class UserOut(BaseModel):
    id: UUID
    login_identifier: str
    status: str
    email_verified_at: datetime | None
    person: PersonOut


class RegisterResponse(BaseModel):
    user: UserOut


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1)


class ResendVerificationRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class SessionSummaryOut(BaseModel):
    expires_at: datetime


class LoginResponse(BaseModel):
    user: UserOut
    session: SessionSummaryOut
    csrf_token: str


class SessionOut(BaseModel):
    id: UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    status: str
    created_ip_address: str | None
    created_user_agent: str | None
    is_current: bool


class RoleAssignmentOut(BaseModel):
    role_code: str
    club_id: UUID | None
    scope_type: str


class MeResponse(BaseModel):
    user: UserOut
    role_assignments: list[RoleAssignmentOut]


class PasswordResetRequestRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class GenericResultResponse(BaseModel):
    """The intentionally identical, non-enumerating shape used by every
    endpoint that must not reveal whether a given identifier/token
    resolved to anything real (resend-verification, password-reset
    request, and — for the specific "which reason" question — verify-email
    and password-reset confirm's failure path)."""

    status: str = "ok"
