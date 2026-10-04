"""Integration tests for the Achievement Engine (Issue #220, A6/A7/A9/
A11/A12) on real canonical Trip / Event / TripParticipant facts.

- the only approved metric, `completed_trips` (A8): completed Trip
  (`Event.status = completed`) AND `TripParticipant.actual_participation
  = true`, per Person;
- event-driven evaluation through the canonical domain's own write paths
  (`PUT /trips/{id}/participants/{person_id}`, `POST /events/{id}/status`);
- reconciliation creating only missing Awards; idempotency; no automatic
  revoke; inactive Definitions / repeatable Definitions / non-Members
  never awarded automatically; exact Rule / Normative Version provenance.

Self-contained factories, per this codebase's convention.
"""

import datetime
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.achievements import engine
from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.achievements import AchievementAward
from app.db.authorization import Role, UserRoleAssignment
from app.db.event_recurrence import EventOccurrence
from app.db.events import Event, EventParticipation
from app.db.identity import Club, ClubMembership, Person, User
from app.db.session import session_scope
from app.db.trips import Trip, TripParticipant
from app.main import app

from .conftest import requires_postgres

pytestmark = requires_postgres

_LONG_AGO = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
_START = datetime.datetime(2026, 9, 20, 10, 0, tzinfo=datetime.timezone.utc)
_OCCURRENCE_STATUS = {"in_progress": "in_progress", "completed": "completed"}


def _trips_at_least(count: int) -> dict:
    return {"metric": "completed_trips", "operator": ">=", "value": count}


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- factories -------------------------------------------------------------------


def _user_with_roles(session, club: Club, *role_codes: str) -> User:  # type: ignore[no-untyped-def]
    person = Person(last_name="Petrov", first_name=f"P-{uuid.uuid4().hex[:8]}")
    session.add(person)
    session.flush()
    session.add(
        ClubMembership(
            club_id=club.id,
            person_id=person.id,
            membership_type="member",
            status="active",
            joined_at=_LONG_AGO,
        )
    )
    user = User(
        person=person, login_identifier=f"u-{uuid.uuid4().hex[:8]}@example.com", status="active"
    )
    session.add(user)
    session.flush()
    for role_code in role_codes:
        role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
        session.add(UserRoleAssignment(user_id=user.id, role_id=role.id, club_id=club.id))
    session.flush()
    return user


@dataclass(frozen=True)
class World:
    club_id: uuid.UUID
    admin: uuid.UUID
    member_a: uuid.UUID
    member_b: uuid.UUID
    instructor_only: uuid.UUID
    guardian_only: uuid.UUID
    member_and_instructor: uuid.UUID


def _world() -> World:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        admin = _user_with_roles(session, club, "admin")
        member_a = _user_with_roles(session, club, "member")
        member_b = _user_with_roles(session, club, "member")
        instructor = _user_with_roles(session, club, "instructor")
        guardian = _user_with_roles(session, club, "guardian")
        both = _user_with_roles(session, club, "member", "instructor")
        session.commit()
        return World(
            club_id=club.id,
            admin=admin.id,
            member_a=member_a.person_id,
            member_b=member_b.person_id,
            instructor_only=instructor.person_id,
            guardian_only=guardian.person_id,
            member_and_instructor=both.person_id,
        )


def _trip(club_id: uuid.UUID, people: list[uuid.UUID], *, status: str = "in_progress") -> uuid.UUID:
    """A trip Event (with its occurrence and Trip) and one registration per
    Person — canonical facts only; actual participation is recorded later."""
    with session_scope() as session:
        event = Event(
            id=uuid.uuid4(),
            club_id=club_id,
            event_type="trip",
            title="Поход",
            start_at=_START,
            end_at=_START + datetime.timedelta(days=2),
            timezone="UTC",
            status=status,
        )
        session.add(event)
        session.flush()
        session.add(
            EventOccurrence(
                event_id=event.id,
                series_id=None,
                club_id=club_id,
                name=event.title,
                event_type=event.event_type,
                recurrence_anchor_at=event.start_at,
                starts_at=event.start_at,
                ends_at=event.end_at,
                timezone=event.timezone,
                status=_OCCURRENCE_STATUS[status],
            )
        )
        session.add(Trip(event_id=event.id))
        session.flush()
        for person_id in people:
            session.add(
                EventParticipation(
                    event_id=event.id, person_id=person_id, registration_status="registered"
                )
            )
        session.commit()
        return event.id


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _post(client: TestClient, path: str, body: dict | None = None):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1{path}", json=body or {}, headers=_csrf(client))


def _record_participation(
    client: TestClient, event_id: uuid.UUID, person_id: uuid.UUID, value: bool = True
) -> None:
    response = client.put(
        f"/api/v1/trips/{event_id}/participants/{person_id}",
        json={"actual_participation": value},
        headers=_csrf(client),
    )
    assert response.status_code == 200, response.text


def _complete(client: TestClient, event_id: uuid.UUID) -> None:
    response = _post(client, f"/events/{event_id}/status", {"status": "completed"})
    assert response.status_code == 200, response.text


def _completed_trip(client: TestClient, world: World, people: list[uuid.UUID]) -> uuid.UUID:
    """The full canonical flow: in-progress Trip, actual participation
    recorded, then the Event completed."""
    event_id = _trip(world.club_id, people)
    for person_id in people:
        _record_participation(client, event_id, person_id)
    _complete(client, event_id)
    return event_id


def _definition(
    client: TestClient,
    condition: dict,
    *,
    award_method: str = "automatic",
    repeatability: str = "non_repeatable",
    source: str = "club",
    normative_set_version_id: str | None = None,
    activate: bool = True,
) -> tuple[dict, dict]:
    response = _post(client, "/achievements/definitions", {
        "code": f"def-{uuid.uuid4().hex[:8]}",
        "name": "Турист",
        "source": source,
        "award_method": award_method,
        "repeatability": repeatability,
    })
    assert response.status_code == 201, response.text
    definition = response.json()
    body: dict = {"condition": condition}
    if normative_set_version_id is not None:
        body["normative_set_version_id"] = normative_set_version_id
    response = _post(client, f"/achievements/definitions/{definition['id']}/rule-versions", body)
    assert response.status_code == 201, response.text
    rule = response.json()
    assert _post(client, f"/achievements/rule-versions/{rule['id']}/activate").status_code == 200
    if activate:
        activated = _post(client, f"/achievements/definitions/{definition['id']}/activate")
        assert activated.status_code == 200
    return definition, rule


def _awards(client: TestClient, **filters: object) -> list[dict]:
    query = "&".join(f"{key}={value}" for key, value in filters.items())
    response = client.get(f"/api/v1/achievements/awards?page_size=100&{query}")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _reconcile(client: TestClient) -> dict:
    response = _post(client, "/achievements/reconciliation")
    assert response.status_code == 200, response.text
    return response.json()


# --- completed_trips on canonical facts -----------------------------------------------


def test_completed_trips_awards_after_threshold_via_event_driven_flow(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition, rule = _definition(client, _trips_at_least(2))

    _completed_trip(client, world, [world.member_a])
    assert _awards(client) == []

    _completed_trip(client, world, [world.member_a])
    awards = _awards(client)
    assert len(awards) == 1
    award = awards[0]
    assert award["person_id"] == str(world.member_a)
    assert award["definition_id"] == definition["id"]
    assert award["award_method"] == "automatic"
    assert award["rule_version_id"] == rule["id"]
    assert award["rule_version_number"] == 1
    assert award["evaluation_trigger"] == "event"
    assert award["evaluated_metrics"] == {"completed_trips": 2}
    assert award["awarded_by_user_id"] is None
    assert award["status"] == "active"


def test_actual_participation_in_already_completed_trip_triggers_award(
    client: TestClient,
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1))
    event_id = _trip(world.club_id, [world.member_a], status="completed")
    _record_participation(client, event_id, world.member_a)
    assert [award["person_id"] for award in _awards(client)] == [str(world.member_a)]


def test_trip_not_yet_completed_does_not_count(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1))
    event_id = _trip(world.club_id, [world.member_a])
    _record_participation(client, event_id, world.member_a)
    assert _awards(client) == []
    _complete(client, event_id)
    assert len(_awards(client)) == 1


def test_registration_without_actual_participation_does_not_count(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1))
    event_id = _trip(world.club_id, [world.member_a, world.member_b])
    _record_participation(client, event_id, world.member_a, value=False)
    _complete(client, event_id)
    assert _awards(client) == []
    assert _reconcile(client)["awards_created"] == 0


def test_only_members_are_awarded_automatically(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1))
    people = [
        world.member_a,
        world.instructor_only,
        world.guardian_only,
        world.member_and_instructor,
    ]
    _completed_trip(client, world, people)
    _reconcile(client)
    awarded = sorted(award["person_id"] for award in _awards(client))
    assert awarded == sorted([str(world.member_a), str(world.member_and_instructor)])


# --- Definition / Rule applicability -----------------------------------------------


def test_inactive_definition_is_ignored(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition, _rule = _definition(client, _trips_at_least(1), activate=False)
    _completed_trip(client, world, [world.member_a])
    assert _reconcile(client)["awards_created"] == 0
    assert _awards(client) == []

    # Reactivation: reconciliation now finds the missing Award.
    _post(client, f"/achievements/definitions/{definition['id']}/activate")
    assert _reconcile(client)["awards_created"] == 1


def test_definition_without_active_rule_version_awards_nothing(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _unused, rule = _definition(client, _trips_at_least(1))
    _post(client, f"/achievements/rule-versions/{rule['id']}/deactivate")
    _completed_trip(client, world, [world.member_a])
    assert _reconcile(client)["awards_created"] == 0


def test_manual_only_definition_is_never_awarded_automatically(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1), award_method="manual")
    _completed_trip(client, world, [world.member_a])
    assert _reconcile(client)["awards_created"] == 0


def test_repeatable_definition_is_not_awarded_automatically(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition, _rule = _definition(
        client, _trips_at_least(1), award_method="both", repeatability="repeatable"
    )
    _completed_trip(client, world, [world.member_a])
    _completed_trip(client, world, [world.member_a])
    assert _reconcile(client)["awards_created"] == 0
    assert _awards(client) == []

    manual = _post(client, "/achievements/awards", {
        "definition_id": definition["id"], "person_id": str(world.member_a)
    })
    assert manual.status_code == 201
    assert manual.json()["award_method"] == "manual"


def test_award_keeps_exact_rule_version_after_a_new_version(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition, v1 = _definition(client, _trips_at_least(1))
    _completed_trip(client, world, [world.member_a])
    first = _awards(client, person_id=world.member_a)[0]
    assert first["rule_version_id"] == v1["id"]

    v2 = _post(client, f"/achievements/definitions/{definition['id']}/rule-versions", {
        "condition": _trips_at_least(1)
    }).json()
    _post(client, f"/achievements/rule-versions/{v2['id']}/activate")
    _completed_trip(client, world, [world.member_b])
    _reconcile(client)

    assert _awards(client, person_id=world.member_a)[0]["rule_version_id"] == v1["id"]
    assert _awards(client, person_id=world.member_b)[0]["rule_version_id"] == v2["id"]


# --- FSTR normative applicability (A11) ------------------------------------------------


def _normative_version(client: TestClient, **overrides: str) -> dict:
    normative_set = _post(client, "/achievements/normative-sets", {
        "code": f"fstr-{uuid.uuid4().hex[:6]}", "name": "ФСТР"
    }).json()
    response = _post(client, f"/achievements/normative-sets/{normative_set['id']}/versions", {
        "source_organization": "ФСТР",
        "document_title": "Нормативы",
        "source_url": "https://tssr.ru/child/",
        "document_version": "test",
        "effective_from": "2025-01-01",
        **overrides,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_fstr_rule_needs_an_active_effective_normative_version(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    normative = _normative_version(client)
    _unused, rule = _definition(
        client, _trips_at_least(1), source="fstr", normative_set_version_id=normative["id"]
    )
    _completed_trip(client, world, [world.member_a])
    # Normative version created inactive: never applicable by itself.
    assert _awards(client) == []
    assert _reconcile(client)["awards_created"] == 0

    _post(client, f"/achievements/normative-versions/{normative['id']}/activate")
    assert _reconcile(client)["awards_created"] == 1
    award = _awards(client)[0]
    assert award["rule_version_id"] == rule["id"]
    assert award["normative_set_version_id"] == normative["id"]
    assert award["evaluation_trigger"] == "reconciliation"


@pytest.mark.parametrize(
    "dates",
    [
        {"effective_from": "2999-01-01"},
        {"effective_from": "2020-01-01", "effective_to": "2020-12-31"},
    ],
)
def test_fstr_rule_outside_normative_effective_period_awards_nothing(
    client: TestClient, dates: dict
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    normative = _normative_version(client, **dates)
    _post(client, f"/achievements/normative-versions/{normative['id']}/activate")
    _definition(client, _trips_at_least(1), source="fstr", normative_set_version_id=normative["id"])
    _completed_trip(client, world, [world.member_a])
    assert _reconcile(client)["awards_created"] == 0


# --- reconciliation, idempotency, historical safety (A2/A7) ---------------------------


def _insert_facts_bypassing_triggers(world: World, person_id: uuid.UUID) -> None:
    """Canonical facts written without the event-driven hooks — a missed
    event the reconciliation must catch up on."""
    event_id = _trip(world.club_id, [person_id], status="completed")
    with session_scope() as session:
        participation_id = session.execute(
            select(EventParticipation.id).where(
                EventParticipation.event_id == event_id,
                EventParticipation.person_id == person_id,
            )
        ).scalar_one()
        session.add(
            TripParticipant(
                event_participation_id=participation_id,
                event_id=event_id,
                actual_participation=True,
            )
        )
        session.commit()


def test_reconciliation_creates_missing_award_once(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1))
    _insert_facts_bypassing_triggers(world, world.member_a)
    assert _awards(client) == []

    result = _reconcile(client)
    assert result["awards_created"] == 1
    assert _awards(client)[0]["evaluation_trigger"] == "reconciliation"
    assert _reconcile(client)["awards_created"] == 0
    assert len(_awards(client)) == 1


def test_duplicate_processing_is_idempotent(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1))
    event_id = _completed_trip(client, world, [world.member_a])

    # The same canonical change processed again, through every path.
    _record_participation(client, event_id, world.member_a)
    with session_scope() as session:
        for _ in range(3):
            engine.handle_tourism_facts_changed(
                session, person_ids=[world.member_a], changed_metrics=frozenset({"completed_trips"})
            )
        engine.reconcile(session)
    assert len(_awards(client)) == 1


def test_only_affected_definitions_are_evaluated(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1))
    _insert_facts_bypassing_triggers(world, world.member_a)
    with session_scope() as session:
        unrelated = engine.handle_tourism_facts_changed(
            session, person_ids=[world.member_a], changed_metrics=frozenset({"other_metric"})
        )
        assert (unrelated.evaluated_rules, unrelated.awards_created) == (0, 0)
        related = engine.handle_tourism_facts_changed(
            session, person_ids=[world.member_a], changed_metrics=frozenset({"completed_trips"})
        )
        assert (related.evaluated_rules, related.awards_created) == (1, 1)


def test_revoked_non_repeatable_award_is_not_recreated(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1))
    _completed_trip(client, world, [world.member_a])
    award = _awards(client)[0]
    _post(client, f"/achievements/awards/{award['id']}/revoke", {"reason": "Ошибка в походе"})

    _completed_trip(client, world, [world.member_a])
    assert _reconcile(client)["awards_created"] == 0
    awards = _awards(client)
    assert [(item["id"], item["status"]) for item in awards] == [(award["id"], "revoked")]


def test_revocation_does_not_touch_canonical_facts(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1))
    event_id = _completed_trip(client, world, [world.member_a])
    award = _awards(client)[0]
    _post(client, f"/achievements/awards/{award['id']}/revoke", {"reason": "Ошибка"})
    with session_scope() as session:
        assert session.get(Event, event_id).status == "completed"  # type: ignore[union-attr]
        facts = session.execute(
            select(TripParticipant.actual_participation).where(TripParticipant.event_id == event_id)
        ).scalars().all()
        assert facts == [True]


def test_engine_never_revokes_or_rewrites_existing_awards(client: TestClient) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition, rule = _definition(client, _trips_at_least(1))
    event_id = _completed_trip(client, world, [world.member_a])
    before = _awards(client)[0]

    # Source facts change, the rule changes, the definition is deactivated
    # and reactivated — the historical Award stays exactly as issued.
    with session_scope() as session:
        session.execute(
            update(TripParticipant)
            .where(TripParticipant.event_id == event_id)
            .values(actual_participation=False)
        )
        session.commit()
    stricter = _post(client, f"/achievements/definitions/{definition['id']}/rule-versions", {
        "condition": _trips_at_least(5)
    }).json()
    _post(client, f"/achievements/rule-versions/{stricter['id']}/activate")
    _post(client, f"/achievements/definitions/{definition['id']}/deactivate")
    _reconcile(client)
    _post(client, f"/achievements/definitions/{definition['id']}/activate")
    _reconcile(client)

    after = _awards(client)
    assert after == [before]
    assert after[0]["rule_version_id"] == rule["id"]


def test_trigger_failure_never_blocks_the_canonical_fact(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    _definition(client, _trips_at_least(1))

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("engine down")

    monkeypatch.setattr(engine, "handle_tourism_facts_changed", _boom)
    event_id = _completed_trip(client, world, [world.member_a])
    monkeypatch.undo()

    with session_scope() as session:
        assert session.get(Event, event_id).status == "completed"  # type: ignore[union-attr]
    assert _awards(client) == []
    assert _reconcile(client)["awards_created"] == 1


# --- database invariants -------------------------------------------------------------


def test_database_rejects_duplicate_non_repeatable_and_automatic_repeatable(
    client: TestClient,
) -> None:
    world = _world()
    _authenticate_as(world.admin)
    definition, rule = _definition(client, _trips_at_least(1))
    _completed_trip(client, world, [world.member_a])

    with session_scope() as session:
        session.add(
            AchievementAward(
                definition_id=uuid.UUID(definition["id"]),
                definition_repeatability="non_repeatable",
                person_id=world.member_a,
                award_method="automatic",
                rule_version_id=uuid.UUID(rule["id"]),
                evaluation_trigger="reconciliation",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    repeatable, repeatable_rule = _definition(
        client, _trips_at_least(1), award_method="both", repeatability="repeatable"
    )
    with session_scope() as session:
        session.add(
            AchievementAward(
                definition_id=uuid.UUID(repeatable["id"]),
                definition_repeatability="repeatable",
                person_id=world.member_a,
                award_method="automatic",
                rule_version_id=uuid.UUID(repeatable_rule["id"]),
                evaluation_trigger="event",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
