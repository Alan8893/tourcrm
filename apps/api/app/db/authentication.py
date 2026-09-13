"""Authentication persistence: AuthenticatedSession,
EmailVerificationChallenge, PasswordResetChallenge (Issue #33).

Canonical source: docs/03-architecture/authentication-persistence.md
(§3 authenticated_sessions, §4 email_verification_challenges, §5
password_reset_challenges), which this module follows field-for-field.
See also ADR-0009 (application-managed authentication) and ADR-0010
(UUID primary keys).

Only a secure hash of every session/challenge secret is ever persisted
here (app.authentication.tokens.hash_token) — the raw value exists only
transiently in the request/response boundary that issues it.

`ON DELETE RESTRICT` on every `user_id` FK: authentication-persistence.md
§7 requires "restrictive historical semantics rather than destructive
cascade" and explicitly that "deleting a User must not silently leave an
active session usable" — RESTRICT means a User with session/challenge
history cannot be deleted at all, matching the same protective pattern
already used for Person/ClubMembership/UserRoleAssignment.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# authentication-persistence.md §3: the authenticated_sessions lifecycle.
SESSION_STATUSES = ("active", "revoked", "expired")


class AuthenticatedSession(Base):
    """One server-side authenticated application session.

    docs/03-architecture/authentication-persistence.md §3.
    """

    __tablename__ = "authenticated_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    session_token_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    revoked_reason: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)
    created_ip_address: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)
    created_user_agent: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    last_seen_ip_address: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('active','revoked','expired')",
            name="ck_authenticated_sessions_status_valid",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_authenticated_sessions_expires_after_created"
        ),
        # "revoked_at is required when status = revoked".
        sa.CheckConstraint(
            "status <> 'revoked' OR revoked_at IS NOT NULL",
            name="ck_authenticated_sessions_revoked_at_when_revoked",
        ),
        sa.Index("ix_authenticated_sessions_user_id_status", "user_id", "status"),
        sa.Index("ix_authenticated_sessions_expires_at_status", "expires_at", "status"),
    )


class EmailVerificationChallenge(Base):
    """A single email-verification attempt/challenge.

    docs/03-architecture/authentication-persistence.md §4.
    """

    __tablename__ = "email_verification_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.Index("ix_email_verification_challenges_user_id_expires_at", "user_id", "expires_at"),
        sa.Index(
            "ix_email_verification_challenges_user_id_consumed_revoked",
            "user_id",
            "consumed_at",
            "revoked_at",
        ),
    )


class PasswordResetChallenge(Base):
    """A single password-reset attempt/challenge.

    docs/03-architecture/authentication-persistence.md §5.
    """

    __tablename__ = "password_reset_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.Index("ix_password_reset_challenges_user_id_expires_at", "user_id", "expires_at"),
        sa.Index(
            "ix_password_reset_challenges_user_id_consumed_revoked",
            "user_id",
            "consumed_at",
            "revoked_at",
        ),
    )


__all__ = [
    "SESSION_STATUSES",
    "AuthenticatedSession",
    "EmailVerificationChallenge",
    "PasswordResetChallenge",
]
