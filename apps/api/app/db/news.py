"""News / Announcements persistence (TH-0120 / Issue #227).

Canonical source: docs/04-ux/news.md §2/§5/§6 (and Issue #227's contract).

`News` is a Club-owned domain object (not Home-only state). Its lifecycle
is the closed `draft` | `published` | `archived` vocabulary (news.md §5);
transitions live in app.news.lifecycle. There is no physical deletion:
"delete" is the `archived` state, and every foreign key here is RESTRICT
so a News row can never be cascaded away.

Audience (news.md §2): `audience_type` is `club` or `groups`. For `groups`,
the *selected groups* are stored in `news_group_targets` — the News'
own targeting configuration, the same shape as the existing
`event_group_targets` (app.db.events.EventGroupTarget). It is deliberately
NOT an audience-membership table: who actually sees a group-targeted News
is resolved at read time from the existing GroupMembership /
GuardianRelationship / GroupInstructorAssignment relationships
(app.news.visibility), never materialized per person.

The optional image references the existing, domain-neutral `files` table
(ADR-0040) — the binary lives behind the `FileStorage` port, exactly like
the Person profile photo (app.people.photo). The optional linked Event is a
plain FK to `events`.
"""

import uuid
from datetime import date, datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.news.vocabulary import CANONICAL_NEWS_AUDIENCE_TYPES, CANONICAL_NEWS_STATUSES

_STATUS_VALUES = ",".join(f"'{value}'" for value in sorted(CANONICAL_NEWS_STATUSES))
_AUDIENCE_VALUES = ",".join(f"'{value}'" for value in sorted(CANONICAL_NEWS_AUDIENCE_TYPES))


class News(Base):
    """One club announcement (news.md §6 field list)."""

    __tablename__ = "news"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    audience_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    # Set once, on the draft -> published transition (system field).
    published_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    event_date: Mapped[Optional[date]] = mapped_column(sa.Date, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="RESTRICT"), nullable=True
    )
    image_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
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
        sa.CheckConstraint(f"status IN ({_STATUS_VALUES})", name="ck_news_status_valid"),
        sa.CheckConstraint(
            f"audience_type IN ({_AUDIENCE_VALUES})", name="ck_news_audience_type_valid"
        ),
        sa.CheckConstraint(
            "status <> 'published' OR published_at IS NOT NULL",
            name="ck_news_published_has_published_at",
        ),
        sa.CheckConstraint(
            "status <> 'archived' OR archived_at IS NOT NULL",
            name="ck_news_archived_has_archived_at",
        ),
        sa.Index("ix_news_club_id_status_published_at", "club_id", "status", "published_at"),
        sa.Index("ix_news_event_id", "event_id"),
    )


class NewsGroupTarget(Base):
    """One selected Group of a `groups`-audience News (news.md §2/§6
    "selected groups"). Targeting configuration only — see module
    docstring."""

    __tablename__ = "news_group_targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    news_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("news.id", ondelete="RESTRICT"), nullable=False
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint("news_id", "group_id", name="uq_news_group_targets_news_id_group_id"),
        sa.Index("ix_news_group_targets_group_id", "group_id"),
    )


__all__ = ["News", "NewsGroupTarget"]
