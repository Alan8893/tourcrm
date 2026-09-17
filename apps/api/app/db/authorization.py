"""Authorization foundation: Role, Permission, RolePermission,
UserRoleAssignment (Issue #19).

Canonical sources: docs/03-architecture/domain-model.md §7,
docs/03-architecture/database-schema.md §6, docs/03-architecture/data-model.md §4,
docs/02-requirements/roles-and-permissions.md, docs/02-requirements/business-rules.md §8,
ADR-0005 (User -> Role -> Permission -> Scope), ADR-0010 (UUID primary keys),
ADR-0013 (canonical scope vocabulary).

This is persistence/domain foundation only, per ADR-0005's model
`User -> Role -> Permission -> Scope`: Role is a named set of Permissions
(via RolePermission); UserRoleAssignment attaches a Role to a User, with an
independent `scope_type`/`scope_ref_id` describing how far that assignment
reaches. A user may hold several roles/assignments at once; effective
permissions are the union of assigned roles' permissions (resolved by a
future authorization layer, not here).

Non-goals here (see Issue #19): authentication, authorization enforcement,
API endpoints, explicit deny, object-level authorization, GuardianRelationship,
groups, bootstrap administrator, automatic user/membership/role-assignment
creation.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.identity import Club, User

# ADR-0013's canonical scope vocabulary. `assigned_events` is only an alias
# of `own_events` and `own_records` is not a global scope (ADR-0013) — so
# neither appears here; a new scope requires updating that ADR first.
CANONICAL_SCOPE_TYPES = ("all", "self", "children", "own_groups", "own_events", "none")

# database-schema.md §6/roles-and-permissions.md §3: the four baseline roles.
# Issue #19: the canonical code is `admin`, not `administrator` (that word
# was only used informally in Issue #3's prose).
BASELINE_ROLE_CODES = ("admin", "instructor", "member", "guardian")

# roles-and-permissions.md §4: the documented permission catalog. Issue #19
# is explicit that only this set is implemented — no invented permissions.
# `guardian_relationship.read`/`guardian_relationship.manage` were added by
# ADR-0025 §2 (Issue #64) — the migration seeding this table
# (65aa6d48bf08) catches up the two codes that e5ae1ad9e1e1's original
# seed predates.
# ADR-0026 §1/Issue #74: the temporal-uniqueness exclusion constraint below
# must treat two NULL `club_id`/`scope_ref_id` values as equal (a global
# assignment must still conflict with another overlapping global
# assignment for the same user/role/scope_type) — but PostgreSQL 16's
# `EXCLUDE USING gist` does not support the `NULLS NOT DISTINCT` clause
# that `uq_user_role_assignments_no_duplicate` used below (that clause is
# UNIQUE/index-only as of PG16; verified empirically against this
# project's own PostgreSQL 16 instance — `ALTER TABLE ... EXCLUDE ...
# NULLS NOT DISTINCT` raises a syntax error). The standard, well-known
# PostgreSQL workaround is to fold NULL to a fixed sentinel value inside
# the constraint expression via `COALESCE`, so two NULLs compare equal
# under `=` for exclusion purposes without changing the column's actual
# nullability or stored data. This sentinel is never a real `Club`/
# scope-reference id (ADR-0010: application-generated UUIDs are random
# v4s, astronomically unlikely to collide with the all-zero UUID).
_NULL_SENTINEL_UUID = "00000000-0000-0000-0000-000000000000"

DOCUMENTED_PERMISSION_CODES = (
    "person.read",
    "person.create",
    "person.update",
    "membership.read",
    "membership.manage",
    "guardian_relationship.read",
    "guardian_relationship.manage",
    "group.read",
    "group.manage",
    "event.read",
    "event.create",
    "event.update",
    "event.cancel",
    "event.manage",
    "attendance.read",
    "attendance.update",
    "trip.read",
    "trip.manage",
    "achievement.read",
    "achievement.award",
    "knowledge.read",
    "knowledge.manage",
    "document.read",
    "document.manage",
    "consent.read",
    "consent.manage",
    "equipment.read",
    "equipment.manage",
    "finance.read",
    "finance.manage",
    "notification.read",
    "notification.manage",
    "audit.read",
    "settings.manage",
    "role.manage",
)


class Role(Base):
    """A named, reusable set of Permissions.

    docs/03-architecture/domain-model.md §7; database-schema.md §6.1 `roles`.

    No `created_at`/`updated_at`: unlike `clubs`/`persons`/`users`/
    `club_memberships` (database-schema.md §5, each of which explicitly
    lists timestamps), §6.1's field list for `roles` does not include them
    — treated as a deliberate omission for this catalog/reference table,
    not an oversight (see final report "Documentation follow-up").
    """

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    # database-schema.md §28: reference values must have a human-readable
    # name, so `name` (unlike `description`) is required.
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )

    # passive_deletes=True: role_id is part of role_permissions' own primary
    # key, so it cannot be nulled out by the ORM's own dependency processor
    # on delete (unlike a plain FK) — the database's own ON DELETE CASCADE
    # (see RolePermission) must handle it instead.
    role_permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", passive_deletes=True
    )
    assignments: Mapped[list["UserRoleAssignment"]] = relationship(back_populates="role")


class Permission(Base):
    """An atomic right to perform an action on a resource (`<resource>.<action>`).

    docs/03-architecture/domain-model.md §7; database-schema.md §6.2 `permissions`;
    roles-and-permissions.md §4 for the documented catalog.

    No `name` field: §6.2's explicit field list is only `id`, `code`,
    `description` (unlike `roles`, which does list `name`) — `description`
    is this entity's human-readable text. No timestamps, for the same
    reason as `Role` above.
    """

    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(sa.String(128), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)

    role_permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="permission", passive_deletes=True
    )


class RolePermission(Base):
    """Role <-> Permission grant.

    docs/03-architecture/domain-model.md §7; database-schema.md §6.3
    `role_permissions`; data-model.md §4.

    Composite primary key `(role_id, permission_id)`, no separate surrogate
    `id` — database-schema.md §6.3 states the primary key explicitly as
    this pair, unlike every other table in this codebase so far (see final
    report "Documentation follow-up" re: data-model.md §2.1's general "every
    entity has an id" statement). The pair PK also is the duplicate-grant
    protection Issue #19 requires — a duplicate insert violates it directly.

    `ON DELETE CASCADE` on both FKs: database-schema.md §24 permits cascade
    on association tables "when the child has no standalone historical
    meaning" — a role-permission grant has no meaning independent of its
    role and permission still existing.
    """

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )

    role: Mapped["Role"] = relationship(back_populates="role_permissions")
    permission: Mapped["Permission"] = relationship(back_populates="role_permissions")


class UserRoleAssignment(Base):
    """A Role assigned to a User, with an independent scope and a
    historical `[valid_from, valid_to)` validity interval (Issue #74,
    implementing ADR-0026 §1).

    docs/03-architecture/domain-model.md §7; database-schema.md §6.4
    `user_role_assignments`; ADR-0005 (`User -> Role -> Permission -> Scope`);
    ADR-0013 (canonical scope vocabulary); ADR-0026 §1 (temporal validity/
    revoke semantics); docs/02-requirements/roles-and-permissions.md §19.

    Scope is a property of the assignment, not of the role itself — a role
    is a fixed set of permissions; `scope_type`/`scope_ref_id` say how far a
    *particular* assignment of that role reaches. Explicit deny is out of
    scope (roles-and-permissions.md §13 / auth-and-authorization.md §15:
    role permissions are additive; a deny model needs its own ADR).

    `valid_from`/`valid_to`: `valid_to = NULL` is an open-ended, currently
    effective assignment. Revoke never deletes or mutates `valid_from`; it
    sets `valid_to` to the server UTC time of the revoke (see
    app.role_assignments.service.revoke_role_assignment) — the row remains
    as immutable history, matching the identical pattern already used for
    `GroupMembership`/`GroupInstructorAssignment`/`EventStaffAssignment`/
    `ClubMembership`/`GuardianRelationship`. Re-assignment after revoke is
    a new row, never a reopened interval.

    `valid_from` has a `server_default` (unlike its sibling temporal
    entities, none of which do): those entities accept a *client-supplied*
    `valid_from` at creation (e.g. people-api.md §14/§15's documented
    request bodies), so a DB default would never actually apply and was
    deliberately omitted to force an explicit value. roles-and-
    permissions.md §19.1 instead documents `valid_from` as
    "серверно/доменом определяемое" (server/domain-determined) — never
    client-suppliable — so app.role_assignments.service always sets it to
    `now()` explicitly on create; the `server_default` here exists purely
    as a robustness backstop (e.g. for the many pre-existing test helpers
    across other domains' test suites that construct a
    `UserRoleAssignment` directly, out of this Issue's scope to touch, and
    for any future direct-ORM construction) so a row is never left without
    a valid interval start rather than encoding any different business
    rule.
    """

    __tablename__ = "user_role_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # RESTRICT (not CASCADE) on user/role/club: database-schema.md §6.4
    # requires "assignment history must remain auditable", matching §24's
    # general policy of protecting historically/audit-significant rows
    # from cascade deletion.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    # Nullable: database-schema.md §6.4 "club_id FK nullable if global role
    # is supported" — a global (installation-wide) assignment is permitted.
    # ADR-0026 §2: required for every scope_type except `none`.
    club_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("clubs.id", ondelete="RESTRICT"), nullable=True
    )
    scope_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    # No FK: the table `scope_ref_id` points into depends on `scope_type`
    # (e.g. a group vs. an event) and no such table exists yet in this
    # codebase — same reasoning as Person.photo_file_id in Issue #17.
    # ADR-0026 §2: not used by any MVP scope — always NULL in this slice.
    scope_ref_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    # ADR-0026 §1 / roles-and-permissions.md §19.1 — see class docstring
    # for why this (uniquely among this codebase's temporal entities) has
    # a server_default.
    valid_from: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
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
            "scope_type IN ('all','self','children','own_groups','own_events','none')",
            name="ck_user_role_assignments_scope_type_valid",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_user_role_assignments_valid_to_after_valid_from",
        ),
        # ADR-0026 §1/Issue #74: replaces the old unconditional
        # `uq_user_role_assignments_no_duplicate` (Issue #19), which could
        # never represent revoke + historical re-assignment for the same
        # (user, role, club, scope_type, scope_ref_id) tuple. This GiST
        # exclusion constraint is the temporal generalization of that same
        # invariant: it still forbids a *duplicate concurrent* assignment,
        # but now scoped to *overlapping* `[valid_from, valid_to)`
        # intervals rather than forbidding any second row unconditionally
        # — sequential historical intervals and touching boundaries are
        # allowed, exactly like `ck_group_memberships_no_duplicate_active`/
        # `ck_group_instructor_assignments_one_active_primary`
        # (app.db.groups) and `ck_club_memberships_no_overlapping_active`
        # (app.db.identity). `club_id`/`scope_ref_id` are folded through
        # `COALESCE` to `_NULL_SENTINEL_UUID` — see that constant's own
        # comment for why (PG16 `EXCLUDE` has no `NULLS NOT DISTINCT`).
        # Requires `btree_gist`, already created by the identity foundation
        # migration (80dd15675404).
        ExcludeConstraint(
            (sa.column("user_id"), "="),
            (sa.column("role_id"), "="),
            (sa.func.coalesce(sa.column("club_id"), _NULL_SENTINEL_UUID), "="),
            (sa.column("scope_type"), "="),
            (sa.func.coalesce(sa.column("scope_ref_id"), _NULL_SENTINEL_UUID), "="),
            (sa.func.tstzrange(sa.column("valid_from"), sa.column("valid_to")), "&&"),
            using="gist",
            name="ck_user_role_assignments_no_overlapping_active",
        ),
    )

    # One-directional (no back_populates on User/Club): Issue #19 is
    # persistence foundation only, and neither entity's Issue #17 definition
    # needs to change to support it.
    user: Mapped["User"] = relationship(User)
    role: Mapped["Role"] = relationship(back_populates="assignments")
    club: Mapped[Optional["Club"]] = relationship(Club)
