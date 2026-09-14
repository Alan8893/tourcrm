"""Group persistence foundation: Group, GroupMembership,
GroupInstructorAssignment (Issue #41), plus the lifecycle/invariant
constraints closed by the Issue #69 specification gate and implemented
by Issue #71.

Canonical source: docs/03-architecture/adr/ADR-0021-group-persistence-model.md,
which this module follows field-for-field, and docs/05-api/people-api.md
§14-16 (the PO decisions — Issue #69 comment — that close the lifecycle/
invariant questions ADR-0021 deliberately left open). See also
docs/03-architecture/domain-model.md §9, docs/03-architecture/data-model.md
§6, docs/03-architecture/database-schema.md §8,
docs/04-modules/people-and-membership.md §8, ADR-0010 (UUID primary keys).

`GroupMembership` deliberately has no `person_id`, `is_primary` or
`assigned_by` column (ADR-0021 §2): the person is resolved through
`club_membership_id -> ClubMembership.person_id`, which is also what makes
the Club boundary explicit — Group and ClubMembership both belong to the
same Club by construction of the domain, since both ultimately anchor to
a `club_id`.

`GroupInstructorAssignment.role_in_group` remains a plain, unconstrained
string: no canonical enum/CHECK vocabulary exists for it (ADR-0021 §3,
reaffirmed as out of scope by Issue #71 §5), and none is invented here.
`Group.status`/`GroupMembership.membership_status`, by contrast, now have
a closed, CHECK-enforced vocabulary — see each class's own docstring.

Cross-Club integrity (ADR-0022): the database models here remain
structurally independent on purpose — no trigger and no redundant/
denormalized `club_id` column is introduced. ADR-0022 §3 makes Club
ownership an application/service-layer invariant instead, enforced by
one shared mechanism (see app.groups.service) rather than by a database
constraint or by ad-hoc per-caller checks. Constructing a `GroupMembership`
or `GroupInstructorAssignment` directly through this module's ORM classes
(bypassing app.groups.service) does not validate Club ownership — that is
expected per ADR-0022 §3/§8, not an oversight; production write paths must
go through app.groups.service instead.

people-api.md §15.2 (PO decision, Issue #69): a person may belong to
several *different* Groups at once, including with overlapping
`[valid_from, valid_to)` intervals — no constraint restricts that here,
matching ADR-0021 §2's original intent. What IS now restricted (unlike
before this module's Issue #71 update) is a duplicate *active*
`GroupMembership` for the *same* `(group_id, club_membership_id)` pair —
see `GroupMembership`'s own docstring for the exclusion constraint that
enforces it.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# people-api.md §14.1 (PO decision, Issue #69): closed two-value
# vocabulary, single allowed transition `active -> archived`, `archived`
# terminal. No restore/unarchive value or transition exists.
CANONICAL_GROUP_STATUSES: frozenset[str] = frozenset({"active", "archived"})

# people-api.md §15.1 (PO decision, Issue #69): closed two-value
# vocabulary, single allowed transition `active -> ended`, `ended`
# terminal.
CANONICAL_GROUP_MEMBERSHIP_STATUSES: frozenset[str] = frozenset({"active", "ended"})

_GROUP_STATUS_VALUES = ",".join(f"'{value}'" for value in CANONICAL_GROUP_STATUSES)
_GROUP_MEMBERSHIP_STATUS_VALUES = ",".join(
    f"'{value}'" for value in CANONICAL_GROUP_MEMBERSHIP_STATUSES
)


class Group(Base):
    """A club-owned organizational/learning group.

    docs/03-architecture/domain-model.md §9; ADR-0021 §1;
    docs/05-api/people-api.md §14.1 (PO decision, Issue #69) for the
    `status` vocabulary/lifecycle below.
    """

    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    # people-api.md §14.1: `active` | `archived`. `active -> archived` is
    # the only allowed transition (enforced in app.groups.lifecycle, not
    # expressible as a single-row CHECK); `archived` is terminal — no
    # restore/unarchive value exists.
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
        sa.CheckConstraint(
            f"status IN ({_GROUP_STATUS_VALUES})",
            name="ck_groups_status_valid",
        ),
        sa.Index("ix_groups_club_id", "club_id"),
    )


class GroupMembership(Base):
    """Historical ClubMembership<->Group association.

    docs/03-architecture/database-schema.md §8 `group_memberships`;
    ADR-0021 §2. Never mutated destructively: closing a period is a new
    fact (`valid_to` set on the existing row), not a deletion.

    people-api.md §15.1 (PO decision, Issue #69): `membership_status` is
    `active` | `ended`; `active -> ended` is the only allowed transition
    (application-layer, see app.groups.lifecycle — not expressible as a
    single-row CHECK); `ended` is terminal.

    people-api.md §15.2 (PO decision, Issue #69): a person may hold
    simultaneous, even overlapping, `GroupMembership` rows across
    *different* Groups — no constraint restricts that. Within the *same*
    Group, at most one `membership_status = 'active'` row may exist for a
    given `club_membership_id` at a time — enforced by the
    `ck_group_memberships_no_duplicate_active` GiST exclusion constraint
    below, the same mechanism already used for `ClubMembership`'s own
    "no overlapping active memberships" invariant
    (app.db.identity.ClubMembership) and `EventStaffAssignment`'s
    primary-assignment invariant (app.db.events.EventStaffAssignment).
    Scoping the exclusion to `membership_status = 'active'` rows only
    means an `ended` historical row never blocks a new membership period
    for the same Group/ClubMembership pair.
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
    # people-api.md §15.1: canonical field name is `membership_status`,
    # not `status` (ADR-0021 §2).
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
        sa.CheckConstraint(
            f"membership_status IN ({_GROUP_MEMBERSHIP_STATUS_VALUES})",
            name="ck_group_memberships_membership_status_valid",
        ),
        # people-api.md §15.2: requires btree_gist, already created by the
        # identity foundation migration (80dd15675404).
        ExcludeConstraint(
            (sa.column("group_id"), "="),
            (sa.column("club_membership_id"), "="),
            (sa.func.tstzrange(sa.column("valid_from"), sa.column("valid_to")), "&&"),
            where=sa.text("membership_status = 'active'"),
            using="gist",
            name="ck_group_memberships_no_duplicate_active",
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

    people-api.md §16.1-16.2 (PO decision, Issue #69): several active
    instructors, and several active non-primary assignments, are allowed
    per Group. `is_primary` is a temporal-*overlap* invariant, not a
    point-in-time one: for a single `group_id`, no two `is_primary = true`
    rows may have overlapping `[valid_from, valid_to)` periods — a shared
    boundary (one row's `valid_to` equals another's `valid_from`) is NOT
    an overlap, so sequential historical primary assignments are allowed.
    Enforced by the `ck_group_instructor_assignments_one_active_primary`
    GiST exclusion constraint below — deliberately the *same* mechanism
    as `EventStaffAssignment.ck_event_staff_assignments_one_active_primary`
    (app.db.events.EventStaffAssignment), which is itself named as a
    point-in-time-sounding "active primary" but is actually implemented
    with the identical `tstzrange(...) && tstzrange(...)` GiST exclusion
    — i.e. it already has the same interval-overlap semantics this
    invariant requires; only the name differs here for clarity. No
    automatic demotion of an existing primary exists anywhere in this
    codebase — a conflicting write is rejected, not silently resolved
    (see app.groups.service).
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
        # people-api.md §16.2: requires btree_gist, already created by the
        # identity foundation migration (80dd15675404).
        ExcludeConstraint(
            (sa.column("group_id"), "="),
            (sa.func.tstzrange(sa.column("valid_from"), sa.column("valid_to")), "&&"),
            where=sa.text("is_primary = true"),
            using="gist",
            name="ck_group_instructor_assignments_one_active_primary",
        ),
        sa.Index("ix_group_instructor_assignments_group_id", "group_id"),
        sa.Index("ix_group_instructor_assignments_user_id", "user_id"),
    )


__all__ = ["Group", "GroupMembership", "GroupInstructorAssignment"]
