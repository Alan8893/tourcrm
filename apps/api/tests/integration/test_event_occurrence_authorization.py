"""Real PostgreSQL integration tests for the Issue #85 / TH-0082
EventOccurrence authorization scope resolution (ADR-0029, ADR-0030):
`all`/`own_events`/`own_groups`/`self`/`children`/`none`, `assigned_events`
as an alias of `own_events`, guardian relationship correctness, cross-Club
integrity, and IDOR/existence-hiding via
`app.events.series_authorization.build_occurrence_resource_context`/
`occurrence_visibility_filter`.

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from sqlalchemy import select

from app.authorization.service import Authorizer, applicable_assignments
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence, EventSeries
from app.db.groups import Group, GroupInstructorAssignment
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.session import session_scope
from app.events.occurrence_relationships import (
    create_occurrence_group_target,
    create_occurrence_participant,
    create_occurrence_staff_assignment,
)
from app.events.series_authorization import (
    build_occurrence_resource_context,
    occurrence_visibility_filter,
)

from .conftest import requires_postgres

_START = datetime(2026, 1, 5, 18, 0, tzinfo=dt_timezone.utc)


def _make_club_and_user(session) -> tuple[uuid.UUID, uuid.UUID]:
    club = Club(name=f"Club-{uuid.uuid4().hex[:8]}", status="active")
    person = Person(last_name="A", first_name="B")
    session.add_all([club, person])
    session.commit()
    user = User(
        person_id=person.id, login_identifier=f"u-{uuid.uuid4().hex[:8]}@x.example", status="active"
    )
    session.add(user)
    session.commit()
    return club.id, user.id


def _make_person_user(session, *, club_id: uuid.UUID | None = None) -> tuple[Person, User]:
    person = Person(last_name="P", first_name=f"X-{uuid.uuid4().hex[:6]}")
    session.add(person)
    session.commit()
    user = User(
        person_id=person.id,
        login_identifier=f"pu-{uuid.uuid4().hex[:8]}@x.example",
        status="active",
    )
    session.add(user)
    session.commit()
    if club_id is not None:
        session.add(
            ClubMembership(
                club_id=club_id,
                person_id=person.id,
                membership_type="student",
                status="active",
                joined_at=_START - timedelta(days=365),
            )
        )
        session.commit()
    return person, user


def _make_series_v1(session, *, club_id: uuid.UUID, user_id: uuid.UUID, **overrides) -> EventSeries:
    series_id = uuid.uuid4()
    defaults = dict(
        id=series_id,
        root_series_id=series_id,
        supersedes_series_id=None,
        version=1,
        club_id=club_id,
        name="Weekly lesson",
        event_type="lesson",
        series_start_at=_START,
        duration_minutes=90,
        recurrence_rule="FREQ=WEEKLY",
        timezone="Europe/Moscow",
        status="active",
        created_by=user_id,
        updated_by=user_id,
    )
    defaults.update(overrides)
    series = EventSeries(**defaults)
    session.add(series)
    session.commit()
    return series


def _make_occurrence(
    session, *, series_id: uuid.UUID, club_id: uuid.UUID, anchor: datetime, **overrides
):
    defaults = dict(
        series_id=series_id,
        club_id=club_id,
        name="Occurrence",
        event_type="lesson",
        recurrence_anchor_at=anchor,
        starts_at=anchor,
        ends_at=anchor + timedelta(hours=2),
        timezone="Europe/Moscow",
        status="scheduled",
    )
    defaults.update(overrides)
    occ = EventOccurrence(**defaults)
    session.add(occ)
    session.commit()
    return occ


def _grant_permission(
    session,
    *,
    user_id: uuid.UUID,
    permission_code: str,
    scope_type: str,
    club_id: uuid.UUID | None = None,
) -> None:
    permission = session.execute(
        select(Permission).where(Permission.code == permission_code)
    ).scalar_one_or_none()
    if permission is None:
        permission = Permission(code=permission_code)
        session.add(permission)
        session.commit()
    role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
    session.add(role)
    session.commit()
    session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    session.add(
        UserRoleAssignment(user_id=user_id, role_id=role.id, scope_type=scope_type, club_id=club_id)
    )
    session.commit()


# --- Canonical scopes: single-object resolution -----------------------------


@requires_postgres
def test_all_scope_grants_access_within_club_boundary() -> None:
    with session_scope() as s:
        club_id, user_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=user_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        _grant_permission(
            s, user_id=user_id, permission_code="event.read", scope_type="all", club_id=club_id
        )

        context = build_occurrence_resource_context(s, occurrence=occ, user_id=user_id)
        authorizer = Authorizer(session=s, user_id=user_id, permission_code="event.read")
        assert authorizer.is_allowed(context) is True


@requires_postgres
def test_own_events_scope_requires_active_occurrence_staff_assignment() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        _, staff_user = _make_person_user(s, club_id=club_id)
        _grant_permission(
            s,
            user_id=staff_user.id,
            permission_code="event.read",
            scope_type="own_events",
            club_id=club_id,
        )

        # Not yet assigned -> denied.
        context = build_occurrence_resource_context(s, occurrence=occ, user_id=staff_user.id)
        authorizer = Authorizer(session=s, user_id=staff_user.id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False

        create_occurrence_staff_assignment(
            s,
            occurrence_id=occ.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=admin_id,
        )
        context = build_occurrence_resource_context(s, occurrence=occ, user_id=staff_user.id)
        assert authorizer.is_allowed(context) is True


@requires_postgres
def test_assigned_events_alias_behaves_identically_to_own_events() -> None:
    """`assigned_events` is normalized to `own_events` at the write
    boundary (app.authorization.context.normalize_scope_type, applied by
    the role-assignment write path — app.api.v1.role_assignments) before
    ever reaching the database; `user_role_assignments.scope_type`'s own
    CHECK constraint only accepts canonical values, so this proves the
    alias resolves to identical occurrence-authorization behavior via
    that same normalization, not by persisting the alias literally."""
    from app.authorization.context import normalize_scope_type

    assert normalize_scope_type("assigned_events") == "own_events"

    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        _, staff_user = _make_person_user(s, club_id=club_id)
        create_occurrence_staff_assignment(
            s,
            occurrence_id=occ.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=admin_id,
        )
        _grant_permission(
            s,
            user_id=staff_user.id,
            permission_code="event.read",
            scope_type=normalize_scope_type("assigned_events"),
            club_id=club_id,
        )
        assignments = applicable_assignments(s, staff_user.id, "event.read")
        assert assignments[0].scope_type == "own_events"

        context = build_occurrence_resource_context(s, occurrence=occ, user_id=staff_user.id)
        authorizer = Authorizer(session=s, user_id=staff_user.id, permission_code="event.read")
        assert authorizer.is_allowed(context) is True


@requires_postgres
def test_own_groups_scope_requires_group_target_and_instructor_assignment() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        group = Group(
            club_id=club_id, name=f"G-{uuid.uuid4().hex[:6]}", status="active", valid_from=_START
        )
        s.add(group)
        s.commit()
        _, instructor_user = _make_person_user(s, club_id=club_id)
        _grant_permission(
            s,
            user_id=instructor_user.id,
            permission_code="event.read",
            scope_type="own_groups",
            club_id=club_id,
        )

        # No group target yet, no instructor assignment yet -> denied.
        context = build_occurrence_resource_context(s, occurrence=occ, user_id=instructor_user.id)
        authorizer = Authorizer(session=s, user_id=instructor_user.id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False

        create_occurrence_group_target(
            s, occurrence_id=occ.id, group_id=group.id, valid_from=_START, actor_user_id=admin_id
        )
        # Group targeted but requester is not yet an instructor for it -> still denied.
        context = build_occurrence_resource_context(s, occurrence=occ, user_id=instructor_user.id)
        assert authorizer.is_allowed(context) is False

        s.add(
            GroupInstructorAssignment(
                group_id=group.id,
                user_id=instructor_user.id,
                role_in_group="instructor",
                valid_from=_START,
            )
        )
        s.commit()
        context = build_occurrence_resource_context(s, occurrence=occ, user_id=instructor_user.id)
        assert authorizer.is_allowed(context) is True


@requires_postgres
def test_own_groups_scope_rejects_cross_club_group() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        other_club_id, _ = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        # A Group in a DIFFERENT club, targeted via a directly-constructed
        # (bypassing cross-club validation) row — proves the authorization
        # predicate itself also enforces the same-Club invariant, not just
        # the write-path service.
        foreign_group = Group(
            club_id=other_club_id,
            name=f"G-{uuid.uuid4().hex[:6]}",
            status="active",
            valid_from=_START,
        )
        s.add(foreign_group)
        s.commit()
        _, instructor_user = _make_person_user(s, club_id=club_id)
        from app.db.event_recurrence_relationships import EventOccurrenceGroupTarget

        s.add(
            EventOccurrenceGroupTarget(
                occurrence_id=occ.id, group_id=foreign_group.id, valid_from=_START, is_override=True
            )
        )
        s.add(
            GroupInstructorAssignment(
                group_id=foreign_group.id,
                user_id=instructor_user.id,
                role_in_group="instructor",
                valid_from=_START,
            )
        )
        s.commit()
        _grant_permission(
            s,
            user_id=instructor_user.id,
            permission_code="event.read",
            scope_type="own_groups",
            club_id=club_id,
        )

        context = build_occurrence_resource_context(s, occurrence=occ, user_id=instructor_user.id)
        authorizer = Authorizer(session=s, user_id=instructor_user.id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False


@requires_postgres
def test_self_scope_requires_active_occurrence_participation() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        participant_person, participant_user = _make_person_user(s, club_id=club_id)
        _grant_permission(
            s,
            user_id=participant_user.id,
            permission_code="event.read",
            scope_type="self",
            club_id=club_id,
        )

        context = build_occurrence_resource_context(s, occurrence=occ, user_id=participant_user.id)
        authorizer = Authorizer(
            session=s, user_id=participant_user.id, permission_code="event.read"
        )
        assert authorizer.is_allowed(context) is False

        create_occurrence_participant(
            s,
            occurrence_id=occ.id,
            person_id=participant_person.id,
            registration_status="registered",
            valid_from=_START,
            actor_user_id=admin_id,
        )
        context = build_occurrence_resource_context(s, occurrence=occ, user_id=participant_user.id)
        assert authorizer.is_allowed(context) is True


@requires_postgres
def test_children_scope_requires_participation_plus_active_guardian_relationship() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        child_person, _ = _make_person_user(s, club_id=club_id)
        guardian_person, guardian_user = _make_person_user(s, club_id=club_id)
        _grant_permission(
            s,
            user_id=guardian_user.id,
            permission_code="event.read",
            scope_type="children",
            club_id=club_id,
        )
        create_occurrence_participant(
            s,
            occurrence_id=occ.id,
            person_id=child_person.id,
            registration_status="registered",
            valid_from=_START,
            actor_user_id=admin_id,
        )

        # No GuardianRelationship yet -> denied.
        context = build_occurrence_resource_context(s, occurrence=occ, user_id=guardian_user.id)
        authorizer = Authorizer(session=s, user_id=guardian_user.id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False

        s.add(
            GuardianRelationship(
                guardian_person_id=guardian_person.id,
                child_person_id=child_person.id,
                relationship_type="parent",
                status="active",
                valid_from=_START,
            )
        )
        s.commit()
        context = build_occurrence_resource_context(s, occurrence=occ, user_id=guardian_user.id)
        assert authorizer.is_allowed(context) is True


@requires_postgres
def test_children_scope_denies_inactive_guardian_relationship() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        child_person, _ = _make_person_user(s, club_id=club_id)
        guardian_person, guardian_user = _make_person_user(s, club_id=club_id)
        create_occurrence_participant(
            s,
            occurrence_id=occ.id,
            person_id=child_person.id,
            registration_status="registered",
            valid_from=_START,
            actor_user_id=admin_id,
        )
        s.add(
            GuardianRelationship(
                guardian_person_id=guardian_person.id,
                child_person_id=child_person.id,
                relationship_type="parent",
                status="inactive",
                valid_from=_START,
            )
        )
        s.commit()
        _grant_permission(
            s,
            user_id=guardian_user.id,
            permission_code="event.read",
            scope_type="children",
            club_id=club_id,
        )

        context = build_occurrence_resource_context(s, occurrence=occ, user_id=guardian_user.id)
        authorizer = Authorizer(session=s, user_id=guardian_user.id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False


@requires_postgres
def test_children_scope_denies_unrelated_child() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        unrelated_child, _ = _make_person_user(s, club_id=club_id)
        _, guardian_user = _make_person_user(s, club_id=club_id)
        create_occurrence_participant(
            s,
            occurrence_id=occ.id,
            person_id=unrelated_child.id,
            registration_status="registered",
            valid_from=_START,
            actor_user_id=admin_id,
        )
        # No GuardianRelationship at all between guardian_user and this child.
        _grant_permission(
            s,
            user_id=guardian_user.id,
            permission_code="event.read",
            scope_type="children",
            club_id=club_id,
        )

        context = build_occurrence_resource_context(s, occurrence=occ, user_id=guardian_user.id)
        authorizer = Authorizer(session=s, user_id=guardian_user.id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False


@requires_postgres
def test_children_scope_denies_when_child_membership_is_in_a_different_club() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        other_club_id, _ = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        # Child is only a member of a DIFFERENT club.
        child_person, _ = _make_person_user(s, club_id=other_club_id)
        guardian_person, guardian_user = _make_person_user(s, club_id=club_id)
        create_occurrence_participant(
            s,
            occurrence_id=occ.id,
            person_id=child_person.id,
            registration_status="registered",
            valid_from=_START,
            actor_user_id=admin_id,
        )
        s.add(
            GuardianRelationship(
                guardian_person_id=guardian_person.id,
                child_person_id=child_person.id,
                relationship_type="parent",
                status="active",
                valid_from=_START,
            )
        )
        s.commit()
        _grant_permission(
            s,
            user_id=guardian_user.id,
            permission_code="event.read",
            scope_type="children",
            club_id=club_id,
        )

        context = build_occurrence_resource_context(s, occurrence=occ, user_id=guardian_user.id)
        authorizer = Authorizer(session=s, user_id=guardian_user.id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False


@requires_postgres
def test_none_scope_denies_everything() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        _, user = _make_person_user(s, club_id=club_id)
        _grant_permission(
            s, user_id=user.id, permission_code="event.read", scope_type="none", club_id=club_id
        )

        context = build_occurrence_resource_context(s, occurrence=occ, user_id=user.id)
        authorizer = Authorizer(session=s, user_id=user.id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False


@requires_postgres
def test_occurrence_club_id_alone_never_grants_non_all_access() -> None:
    """The occurrence belongs to the requester's own club, but no
    relationship of any kind exists — every non-`all` scope must deny."""
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        _, user = _make_person_user(s, club_id=club_id)

        for scope in ("own_events", "own_groups", "self", "children"):
            _grant_permission(
                s, user_id=user.id, permission_code="event.read", scope_type=scope, club_id=club_id
            )
            context = build_occurrence_resource_context(s, occurrence=occ, user_id=user.id)
            authorizer = Authorizer(session=s, user_id=user.id, permission_code="event.read")
            assert authorizer.is_allowed(context) is False, f"{scope} incorrectly granted access"


# --- List-level filtering / IDOR (occurrence_visibility_filter) ------------


@requires_postgres
def test_occurrence_visibility_filter_excludes_unrelated_occurrences_under_own_events() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        assigned_occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        other_occ = _make_occurrence(
            s, series_id=series.id, club_id=club_id, anchor=_START + timedelta(days=7)
        )
        _, staff_user = _make_person_user(s, club_id=club_id)
        create_occurrence_staff_assignment(
            s,
            occurrence_id=assigned_occ.id,
            user_id=staff_user.id,
            role_in_event="instructor",
            valid_from=_START,
            actor_user_id=admin_id,
        )
        _grant_permission(
            s,
            user_id=staff_user.id,
            permission_code="event.read",
            scope_type="own_events",
            club_id=club_id,
        )

        filt = occurrence_visibility_filter(s, user_id=staff_user.id, permission_code="event.read")
        visible_ids = set(s.execute(select(EventOccurrence.id).where(filt)).scalars().all())
        assert assigned_occ.id in visible_ids
        assert other_occ.id not in visible_ids


@requires_postgres
def test_occurrence_visibility_filter_never_leaks_a_different_clubs_occurrence() -> None:
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        other_club_id, other_admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        other_series = _make_series_v1(s, club_id=other_club_id, user_id=other_admin_id)
        visible_occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        foreign_occ = _make_occurrence(
            s, series_id=other_series.id, club_id=other_club_id, anchor=_START
        )
        _grant_permission(
            s, user_id=admin_id, permission_code="event.read", scope_type="all", club_id=club_id
        )

        filt = occurrence_visibility_filter(s, user_id=admin_id, permission_code="event.read")
        visible_ids = set(s.execute(select(EventOccurrence.id).where(filt)).scalars().all())
        assert visible_ids == {visible_occ.id}
        assert foreign_occ.id not in visible_ids


@requires_postgres
def test_unauthorized_occurrence_is_hidden_not_403() -> None:
    """Matches the existing existence-hiding convention: an occurrence
    outside the requester's scope must be indistinguishable from a
    nonexistent one at the resource-context level (is_allowed() is False,
    not a raised exception carrying identifying information)."""
    with session_scope() as s:
        club_id, admin_id = _make_club_and_user(s)
        series = _make_series_v1(s, club_id=club_id, user_id=admin_id)
        occ = _make_occurrence(s, series_id=series.id, club_id=club_id, anchor=_START)
        _, outsider = _make_person_user(s, club_id=club_id)
        _grant_permission(
            s,
            user_id=outsider.id,
            permission_code="event.read",
            scope_type="own_events",
            club_id=club_id,
        )

        context = build_occurrence_resource_context(s, occurrence=occ, user_id=outsider.id)
        authorizer = Authorizer(session=s, user_id=outsider.id, permission_code="event.read")
        assert authorizer.is_allowed(context) is False
