"""Real PostgreSQL integration tests for the ADR-0022/ADR-0023 Club-
ownership validation service (app.events.service.create_event_group_target)
— the canonical, shared enforcement mechanism for EventGroupTarget
writes. See tests/integration/test_event_group_targets.py for the raw
persistence-layer tests (which deliberately do not enforce this
invariant — see that file's module docstring).

Run with a reachable PostgreSQL instance, matching
tests/integration/test_identity.py:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid

import pytest
from sqlalchemy import select

from app.db.events import Event, EventGroupTarget
from app.db.groups import Group
from app.db.identity import Club
from app.db.session import session_scope
from app.events.service import EventGroupTargetClubMismatchError, create_event_group_target

from .conftest import requires_postgres


def _utc(*args: int) -> datetime.datetime:
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _make_club(**overrides: object) -> Club:
    defaults: dict[str, object] = {
        "name": f"Test Club {uuid.uuid4().hex[:8]}",
        "status": "active",
    }
    defaults.update(overrides)
    return Club(**defaults)  # type: ignore[arg-type]


def _make_event(club: Club, **overrides: object) -> Event:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "event_type": "lesson",
        "title": "Orienteering basics",
        "start_at": _utc(2026, 9, 20, 17, 0),
        "end_at": _utc(2026, 9, 20, 19, 0),
        "timezone": "Europe/Moscow",
        "status": "draft",
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _make_group(club: Club, **overrides: object) -> Group:
    defaults: dict[str, object] = {
        "club_id": club.id,
        "name": f"Test Group {uuid.uuid4().hex[:8]}",
        "status": "active",
        "valid_from": _utc(2024, 1, 1),
    }
    defaults.update(overrides)
    return Group(**defaults)  # type: ignore[arg-type]


# --- same Club: allowed -----------------------------------------------


@requires_postgres
def test_create_event_group_target_succeeds_for_same_club() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group = _make_group(club)
        session.add_all([event, group])
        session.commit()

        target = create_event_group_target(
            session, event_id=event.id, group_id=group.id, valid_from=_utc(2024, 1, 1)
        )

        fetched = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.id == target.id)
        ).scalar_one()
        assert fetched.event_id == event.id
        assert fetched.group_id == group.id


# --- different Clubs: rejected ------------------------------------------


@requires_postgres
def test_create_event_group_target_rejects_cross_club_combination() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        session.add_all([club_a, club_b])
        session.commit()
        event_in_a = _make_event(club_a)
        group_in_b = _make_group(club_b)
        session.add_all([event_in_a, group_in_b])
        session.commit()

        with pytest.raises(EventGroupTargetClubMismatchError) as exc_info:
            create_event_group_target(
                session,
                event_id=event_in_a.id,
                group_id=group_in_b.id,
                valid_from=_utc(2024, 1, 1),
            )
        assert exc_info.value.event_club_id == club_a.id
        assert exc_info.value.group_club_id == club_b.id

        rows = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.event_id == event_in_a.id)
        ).scalars().all()
        assert rows == []


@requires_postgres
def test_create_event_group_target_rejects_when_source_group_is_the_foreign_club() -> None:
    """Same invariant as the cross-Club test above, from the opposite
    direction: the Group being linked is the one that belongs to a
    *different* Club than the Event, not the other way around — the
    check must be symmetric regardless of which side is "foreign".
    """
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        session.add_all([club_a, club_b])
        session.commit()
        event_in_b = _make_event(club_b)
        group_in_a = _make_group(club_a)
        session.add_all([event_in_b, group_in_a])
        session.commit()

        with pytest.raises(EventGroupTargetClubMismatchError):
            create_event_group_target(
                session,
                event_id=event_in_b.id,
                group_id=group_in_a.id,
                valid_from=_utc(2024, 1, 1),
            )

        rows = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.group_id == group_in_a.id)
        ).scalars().all()
        assert rows == []


@requires_postgres
def test_valid_event_group_target_still_works_after_a_rejected_attempt() -> None:
    with session_scope() as session:
        club_a = _make_club()
        club_b = _make_club()
        session.add_all([club_a, club_b])
        session.commit()
        event_in_a = _make_event(club_a)
        group_in_a = _make_group(club_a)
        group_in_b = _make_group(club_b)
        session.add_all([event_in_a, group_in_a, group_in_b])
        session.commit()

        with pytest.raises(EventGroupTargetClubMismatchError):
            create_event_group_target(
                session,
                event_id=event_in_a.id,
                group_id=group_in_b.id,
                valid_from=_utc(2024, 1, 1),
            )

        # The same session/event can still be used correctly afterward —
        # the rollback on rejection did not corrupt the session.
        target = create_event_group_target(
            session, event_id=event_in_a.id, group_id=group_in_a.id, valid_from=_utc(2024, 1, 1)
        )
        assert target.group_id == group_in_a.id


# --- multiple Events/Groups through the service --------------------------


@requires_postgres
def test_create_event_group_target_allows_multiple_groups_for_one_event() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event = _make_event(club)
        group_a = _make_group(club)
        group_b = _make_group(club)
        session.add_all([event, group_a, group_b])
        session.commit()

        create_event_group_target(
            session, event_id=event.id, group_id=group_a.id, valid_from=_utc(2024, 1, 1)
        )
        create_event_group_target(
            session, event_id=event.id, group_id=group_b.id, valid_from=_utc(2024, 1, 1)
        )

        rows = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.event_id == event.id)
        ).scalars().all()
        assert {row.group_id for row in rows} == {group_a.id, group_b.id}


@requires_postgres
def test_create_event_group_target_allows_multiple_events_for_one_group() -> None:
    with session_scope() as session:
        club = _make_club()
        session.add(club)
        session.commit()
        event_a = _make_event(club)
        event_b = _make_event(club)
        group = _make_group(club)
        session.add_all([event_a, event_b, group])
        session.commit()

        create_event_group_target(
            session, event_id=event_a.id, group_id=group.id, valid_from=_utc(2024, 1, 1)
        )
        create_event_group_target(
            session, event_id=event_b.id, group_id=group.id, valid_from=_utc(2024, 1, 1)
        )

        rows = session.execute(
            select(EventGroupTarget).where(EventGroupTarget.group_id == group.id)
        ).scalars().all()
        assert {row.event_id for row in rows} == {event_a.id, event_b.id}
