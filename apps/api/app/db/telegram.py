"""Telegram user linking and update-polling persistence (Issue #329,
ADR-0047 §3.2/§4.2).

- `telegram_identities` — the User <-> Telegram identity association.
  Rows are never deleted: unlink and replacement are lifecycle transitions
  (`status` + `ended_at`). Two partial UNIQUE indexes over the `active`
  rows enforce, at the database level and under concurrency, that one
  Telegram numeric user id is actively linked to at most one User and one
  User has at most one active Telegram identity. `telegram_user_id` is the
  Bot API `User.id` of the sender of a trusted update — never a value a
  browser supplied. For a private chat the Bot API chat id equals that
  user id, so it is also the private delivery address. No Telegram
  username, display name or message content is stored.
- `telegram_link_challenges` — one-time linking challenges. Only the
  SHA-256 hash of the raw token is stored (UNIQUE). A challenge is
  consumed at most once (`status`/`consumed_at` CHECK); expiry is
  `expires_at` against database time. At most one `pending` challenge per
  User (partial UNIQUE): issuing a new one revokes the previous one.
- `telegram_update_checkpoints` — the long-poll offset per bot (keyed by
  the bot's numeric id, which is public — never the bot token): the last
  `update_id` whose processing has committed. No raw update is stored.

`ON DELETE RESTRICT` on every `users` FK, like the authentication
challenges: linking history is never cascaded away.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.telegram.vocabulary import (
    CANONICAL_CHALLENGE_STATUSES,
    CANONICAL_IDENTITY_STATUSES,
    CHALLENGE_CONSUMED,
    CHALLENGE_PENDING,
    CHALLENGE_REVOKED,
    IDENTITY_ACTIVE,
)

ONE_ACTIVE_IDENTITY_PER_TELEGRAM_USER_INDEX = "uq_telegram_identities_active_telegram_user_id"
ONE_ACTIVE_IDENTITY_PER_USER_INDEX = "uq_telegram_identities_active_user_id"
ONE_PENDING_CHALLENGE_PER_USER_INDEX = "uq_telegram_link_challenges_pending_user_id"


def _in(values: frozenset[str]) -> str:
    return ",".join(f"'{value}'" for value in sorted(values))


class TelegramIdentity(Base):
    """A TourCRM User's linked Telegram identity (ADR-0047 §4.2)."""

    __tablename__ = "telegram_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    telegram_user_id: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.CheckConstraint(
            f"status IN ({_in(CANONICAL_IDENTITY_STATUSES)})",
            name="ck_telegram_identities_status_valid",
        ),
        sa.CheckConstraint(
            "telegram_user_id > 0", name="ck_telegram_identities_telegram_user_id_positive"
        ),
        sa.CheckConstraint(
            f"(status = '{IDENTITY_ACTIVE}') = (ended_at IS NULL)",
            name="ck_telegram_identities_ended_at_iff_ended",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= linked_at",
            name="ck_telegram_identities_ended_after_linked",
        ),
        sa.Index(
            ONE_ACTIVE_IDENTITY_PER_TELEGRAM_USER_INDEX,
            "telegram_user_id",
            unique=True,
            postgresql_where=sa.text(f"status = '{IDENTITY_ACTIVE}'"),
        ),
        sa.Index(
            ONE_ACTIVE_IDENTITY_PER_USER_INDEX,
            "user_id",
            unique=True,
            postgresql_where=sa.text(f"status = '{IDENTITY_ACTIVE}'"),
        ),
        sa.Index("ix_telegram_identities_user_id", "user_id"),
        sa.Index("ix_telegram_identities_telegram_user_id", "telegram_user_id"),
    )


class TelegramLinkChallenge(Base):
    """A one-time Telegram linking challenge (ADR-0047 §4.1/§4.3)."""

    __tablename__ = "telegram_link_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # The identity a consumed challenge linked or confirmed.
    telegram_identity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("telegram_identities.id", ondelete="RESTRICT"),
        nullable=True,
    )

    __table_args__ = (
        sa.UniqueConstraint("token_hash", name="uq_telegram_link_challenges_token_hash"),
        sa.CheckConstraint(
            f"status IN ({_in(CANONICAL_CHALLENGE_STATUSES)})",
            name="ck_telegram_link_challenges_status_valid",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_telegram_link_challenges_expires_after_created"
        ),
        sa.CheckConstraint(
            f"(status = '{CHALLENGE_CONSUMED}') = (consumed_at IS NOT NULL)",
            name="ck_telegram_link_challenges_consumed_at_iff_consumed",
        ),
        sa.CheckConstraint(
            f"(status = '{CHALLENGE_REVOKED}') = (revoked_at IS NOT NULL)",
            name="ck_telegram_link_challenges_revoked_at_iff_revoked",
        ),
        sa.CheckConstraint(
            f"(status = '{CHALLENGE_CONSUMED}') = (telegram_identity_id IS NOT NULL)",
            name="ck_telegram_link_challenges_identity_iff_consumed",
        ),
        sa.Index(
            ONE_PENDING_CHALLENGE_PER_USER_INDEX,
            "user_id",
            unique=True,
            postgresql_where=sa.text(f"status = '{CHALLENGE_PENDING}'"),
        ),
        # Per-User issuance rate limit (rolling window over created_at).
        sa.Index("ix_telegram_link_challenges_user_id_created_at", "user_id", "created_at"),
    )


class TelegramUpdateCheckpoint(Base):
    """Durable `getUpdates` progress for one bot (ADR-0047 §3.2).
    `last_update_id` NULL means no update has been processed yet."""

    __tablename__ = "telegram_update_checkpoints"

    bot_id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=False)
    last_update_id: Mapped[Optional[int]] = mapped_column(sa.BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.CheckConstraint("bot_id > 0", name="ck_telegram_update_checkpoints_bot_id_positive"),
        sa.CheckConstraint(
            "last_update_id IS NULL OR last_update_id >= 0",
            name="ck_telegram_update_checkpoints_last_update_id_non_negative",
        ),
    )


__all__ = [
    "ONE_ACTIVE_IDENTITY_PER_TELEGRAM_USER_INDEX",
    "ONE_ACTIVE_IDENTITY_PER_USER_INDEX",
    "ONE_PENDING_CHALLENGE_PER_USER_INDEX",
    "TelegramIdentity",
    "TelegramLinkChallenge",
    "TelegramUpdateCheckpoint",
]
