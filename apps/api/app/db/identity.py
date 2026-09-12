"""Identity foundation: Club, Person, User, ClubMembership (Issue #17).

Canonical sources: docs/03-architecture/domain-model.md §3-6,
docs/03-architecture/database-schema.md §5, docs/02-requirements/business-rules.md §3-4,
docs/07-security/security-and-privacy.md §4.1 (User account lifecycle),
ADR-0005 (identity split), ADR-0010 (UUID primary keys).

These four entities are kept deliberately separate per ADR-0005 and the
Issue's own instruction: Person is a physical person and may exist without
a User; a User is linked to at most one Person; ClubMembership is a
historical Person<->Club link that is never mutated destructively (new
rows record re-joins, not edits to old ones).

Non-goals here (see Issue #17): authentication flows, password hashing
implementation, RBAC/Role/Permission, GuardianRelationship, groups, domain
API endpoints.
"""

import uuid
from datetime import date, datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def normalize_login_identifier(value: str) -> str:
    """The one normalization rule for User.login_identifier: trim
    surrounding whitespace, then lowercase. Mirrored in the database as a
    generated column (User.normalized_login_identifier) so the invariant
    holds even for writes that bypass this helper — this function exists
    for application code that wants to normalize before comparing.
    """
    return value.strip().lower()


class Club(Base):
    """The club/installation boundary.

    docs/03-architecture/domain-model.md §3; database-schema.md §5.1 `clubs`.
    """

    __tablename__ = "clubs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True)
    short_name: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    # No canonical enumeration of Club.status values exists in docs at this
    # foundation stage (see final report "Documentation follow-up") — stored
    # as free text, no CHECK constraint, until product owner defines one.
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    memberships: Mapped[list["ClubMembership"]] = relationship(back_populates="club")


class Person(Base):
    """A physical person, independent of any account.

    docs/03-architecture/domain-model.md §4; database-schema.md §5.2 `persons`.

    Deliberately excludes `status`: database-schema.md §5.2 and Issue #17's
    own field list both omit it, even though data-model.md §3 lists
    `status` among Person's key fields — see final report
    "Documentation follow-up", not silently resolved here.
    """

    __tablename__ = "persons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    last_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    middle_name: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    birth_date: Mapped[Optional[date]] = mapped_column(sa.Date, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(sa.String(32), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(sa.String(255), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    # No `files` table exists yet in this codebase (planned for a later
    # epic — database-schema.md §11 `files`); stored as a plain UUID
    # without an FK constraint until that table exists — see final report
    # "Documentation follow-up".
    photo_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    user: Mapped[Optional["User"]] = relationship(back_populates="person")
    memberships: Mapped[list["ClubMembership"]] = relationship(back_populates="person")


class User(Base):
    """An authentication account.

    docs/03-architecture/domain-model.md §5; database-schema.md §5.3 `users`;
    ADR-0005. At most one User per Person (unique `person_id`); a Person may
    exist without a User. No plaintext password is modeled — only
    `password_hash`; hashing itself is out of scope for this Issue.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False
    )
    login_identifier: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    # Generated (STORED) so the "normalized identifier is unique" invariant
    # holds at the database level regardless of how a row is written —
    # see normalize_login_identifier() above for the mirrored application-
    # level rule (trim + lowercase).
    normalized_login_identifier: Mapped[str] = mapped_column(
        sa.String(255),
        sa.Computed("lower(trim(login_identifier))", persisted=True),
        nullable=False,
    )
    password_hash: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    # Account lifecycle per docs/07-security/security-and-privacy.md §4.1.
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
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
        sa.UniqueConstraint("person_id", name="uq_users_person_id"),
        sa.UniqueConstraint(
            "normalized_login_identifier", name="uq_users_normalized_login_identifier"
        ),
        sa.CheckConstraint(
            "status IN ('pending','active','locked','suspended','disabled','archived')",
            name="ck_users_status_valid",
        ),
    )

    person: Mapped["Person"] = relationship(back_populates="user")


class ClubMembership(Base):
    """Historical Person<->Club membership.

    docs/03-architecture/domain-model.md §6; database-schema.md §5.4
    `club_memberships`; business-rules.md §4. Never mutated destructively:
    status transitions and repeated joins/leaves are new facts, not edits
    to history.
    """

    __tablename__ = "club_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False
    )
    # No canonical enumeration of membership_type values exists in docs at
    # this foundation stage — see final report "Documentation follow-up".
    membership_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    # business-rules.md §4: pending, active, suspended, inactive, archived.
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    left_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True), nullable=True)
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
            "status IN ('pending','active','suspended','inactive','archived')",
            name="ck_club_memberships_status_valid",
        ),
        sa.CheckConstraint(
            "left_at IS NULL OR left_at >= joined_at",
            name="ck_club_memberships_left_at_after_joined_at",
        ),
        # database-schema.md §5.4: "overlapping active memberships of the
        # same type should be prohibited". A partial GiST exclusion
        # constraint is the standard PostgreSQL mechanism for "no two rows
        # with equal keys may have overlapping time ranges" — requires the
        # btree_gist extension (created in the migration). Only applies
        # among status='active' rows, matching the documented wording
        # exactly; a NULL left_at is treated by tstzrange as unbounded
        # (still ongoing), which is the correct semantics here.
        ExcludeConstraint(
            (sa.column("person_id"), "="),
            (sa.column("membership_type"), "="),
            (sa.func.tstzrange(sa.column("joined_at"), sa.column("left_at")), "&&"),
            where=sa.text("status = 'active'"),
            using="gist",
            name="ck_club_memberships_no_overlapping_active",
        ),
    )

    club: Mapped["Club"] = relationship(back_populates="memberships")
    person: Mapped["Person"] = relationship(back_populates="memberships")
