"""Group persistence foundation: Group, GroupMembership,
GroupInstructorAssignment (Issue #41).

Canonical source: docs/03-architecture/adr/ADR-0021-group-persistence-model.md,
which this module follows field-for-field. See also
docs/03-architecture/domain-model.md §9, docs/03-architecture/data-model.md
§6, docs/03-architecture/database-schema.md §8,
docs/04-modules/people-and-membership.md §8, ADR-0010 (UUID primary keys).

`GroupMembership` deliberately has no `person_id`, `is_primary` or
`assigned_by` column (ADR-0021 §2): the person is resolved through
`club_membership_id -> ClubMembership.person_id`, which is also what makes
the Club boundary explicit — Group and ClubMembership both belong to the
same Club by construction of the domain, since both ultimately anchor to
a `club_id`.

`Group.status` and `GroupInstructorAssignment.role_in_group` are plain,
unconstrained strings: no canonical enum/CHECK vocabulary exists for
either yet (ADR-0021 §1/§3), and none is invented here.

Cross-Club integrity: this codebase has no existing DB-level mechanism
(trigger or composite FK) anywhere that enforces "two independently
foreign-keyed columns must resolve to the same Club" — `UserRoleAssignment`
(app.db.authorization) is the closest precedent, and its `club_id` is an
independent column with no such DB-level cross-check either; the only
existing cross-Club check in this codebase
(`app.authorization.service.club_boundary_matches`) is a plain Python
function at the application/service layer, not a database constraint.
Consistent with that precedent, this persistence foundation does not
introduce a new trigger-based mechanism (Issue #41 does not authorize
inventing one), and Group has no API/service layer yet to place an
application-level check in (also an explicit non-goal). The FK structure
here still makes a cross-Club combination fully detectable by joining
`Group.club_id` against `ClubMembership.club_id` — see
tests/integration/test_groups.py for a regression test proving this join
path, and the "Documentation follow-up" note in the final report for the
enforcement-mechanism decision this leaves open for a future issue.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Group(Base):
    """A club-owned organizational/learning group.

    docs/03-architecture/domain-model.md §9; ADR-0021 §1.
    """

    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    # ADR-0021 §1: plain string, no enum/CHECK vocabulary — Group lifecycle
    # transitions require a separate, not-yet-made business-policy decision.
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    valid_to: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True), nullable=True)
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
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_groups_valid_to_after_valid_from",
        ),
        sa.Index("ix_groups_club_id", "club_id"),
    )


class GroupMembership(Base):
    """Historical ClubMembership<->Group association.

    docs/03-architecture/database-schema.md §8 `group_memberships`;
    ADR-0021 §2. Never mutated destructively: closing a period is a new
    fact (`valid_to` set on the existing row), not a deletion.
    """

    __tablename__ = "group_memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="RESTRICT"), nullable=False
    )
    club_membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("club_memberships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    valid_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    valid_to: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    # ADR-0021 §2: canonical field name is `membership_status`, not
    # `status`; plain string, no enum/CHECK vocabulary defined yet.
    membership_status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
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
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_group_memberships_valid_to_after_valid_from",
        ),
        # database-schema.md §8 `group_memberships` Rules: "one membership
        # can move between groups over time" / "overlapping current
        # assignments should be rejected unless explicitly allowed". No
        # `membership_status` vocabulary is canonical yet, so — unlike
        # ClubMembership's analogous exclusion constraint, which can filter
        # `WHERE status = 'active'` — this cannot be scoped to a specific
        # status value without inventing one; it is scoped purely
        # structurally: no two rows for the same club_membership_id may
        # have overlapping [valid_from, valid_to) ranges, regardless of
        # which group_id they name.
        ExcludeConstraint(
            (sa.column("club_membership_id"), "="),
            (sa.func.tstzrange(sa.column("valid_from"), sa.column("valid_to")), "&&"),
            using="gist",
            name="ck_group_memberships_no_overlapping_periods",
        ),
        # database-schema.md §23: "group memberships by (group_id,
        # valid_from, valid_to) as operationally required".
        sa.Index(
            "ix_group_memberships_group_id_valid_from_valid_to",
            "group_id",
            "valid_from",
            "valid_to",
        ),
        sa.Index("ix_group_memberships_club_membership_id", "club_membership_id"),
    )


class GroupInstructorAssignment(Base):
    """Explicit instructor/responsible-person assignment to a Group.

    docs/03-architecture/data-model.md §6 `GroupInstructorAssignment`;
    ADR-0021 §3. Global `instructor` role membership is never sufficient
    to establish responsibility for a group — this table is the only
    source of truth for that relationship.
    """

    __tablename__ = "group_instructor_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="RESTRICT"), nullable=False
    )
    # ADR-0021 §3: `user_id`, not `person_id` — authorization responsibility
    # belongs to an authenticated User principal.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # ADR-0021 §3: intentionally not a closed enum — the allowed vocabulary
    # must be reconciled with the Event responsibility model first.
    role_in_group: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    valid_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    valid_to: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True), nullable=True)
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
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_group_instructor_assignments_valid_to_after_valid_from",
        ),
        sa.Index("ix_group_instructor_assignments_group_id", "group_id"),
        sa.Index("ix_group_instructor_assignments_user_id", "user_id"),
    )


__all__ = ["Group", "GroupMembership", "GroupInstructorAssignment"]
