"""Canonical Participant Export dataset queries (participant-export-api.md
§3, §4, §8; GAP-2/GAP-3/GAP-4).

One result row per Person (§8, GAP-3): context membership/participation
records are only ever used inside `EXISTS` predicates or through
EventParticipation's `UNIQUE(event_id, person_id)`, so historical or
duplicate ClubMembership/GroupMembership rows can never multiply a Person.

Context semantics:

- `club` — Persons with a ClubMembership in the Club whose `status` equals
  the membership status filter;
- `group` — Persons with a GroupMembership in the Group (through a
  ClubMembership of the same Club) whose `membership_status` equals the
  filter;
- `event` — Persons with an EventParticipation for the Event (optionally
  narrowed by `registration_status`) and a ClubMembership in the Club with
  the filtered status;
- `group_event` — the intersection GroupMembership ∩ EventParticipation:
  Persons satisfying both the `group` and the participation part of the
  `event` predicate. Event group targeting is not consulted — visibility is
  not participation (ADR-0042).

Filtering happens here, in SQL, before any presentation (§4). Nothing here
authorizes; the caller (app.exports.service) has already done so.
"""

import uuid
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from app.db.events import EventParticipation
from app.db.groups import GroupMembership
from app.db.identity import ClubMembership, GuardianRelationship, Person

# ADR-0023 §3 vocabulary; "active" is used exactly as in
# app.people.guardian_authorization.
_ACTIVE_GUARDIAN_RELATIONSHIP_STATUS = "active"


@dataclass(frozen=True)
class PersonRow:
    person_id: uuid.UUID
    last_name: str
    first_name: str
    middle_name: str | None
    birth_date: date | None
    phone: str | None
    email: str | None
    address: str | None
    # EventParticipation.registration_status in event contexts, else None.
    participation_status: str | None


@dataclass(frozen=True)
class GuardianContact:
    name: str
    phone: str | None


def _club_membership_exists(*, club_id: uuid.UUID, status: str) -> sa.ColumnElement[bool]:
    return sa.exists(
        sa.select(ClubMembership.id).where(
            ClubMembership.person_id == Person.id,
            ClubMembership.club_id == club_id,
            ClubMembership.status == status,
        )
    )


def _group_membership_exists(
    *, club_id: uuid.UUID, group_id: uuid.UUID, status: str
) -> sa.ColumnElement[bool]:
    return sa.exists(
        sa.select(GroupMembership.id)
        .join(ClubMembership, ClubMembership.id == GroupMembership.club_membership_id)
        .where(
            ClubMembership.person_id == Person.id,
            # Club boundary enforced in the predicate itself, not only by
            # ADR-0022's write-time integrity checks.
            ClubMembership.club_id == club_id,
            GroupMembership.group_id == group_id,
            GroupMembership.membership_status == status,
        )
    )


def list_export_persons(
    session: Session,
    *,
    context: str,
    club_id: uuid.UUID,
    group_id: uuid.UUID | None,
    event_id: uuid.UUID | None,
    membership_status: str,
    participation_status: str | None,
) -> list[PersonRow]:
    """Ordered by last name, first name, middle name (then id, for a
    stable order between namesakes)."""
    participation_column = (
        EventParticipation.registration_status.label("participation_status")
        if event_id is not None
        else sa.null().label("participation_status")
    )

    stmt = sa.select(
        Person.id,
        Person.last_name,
        Person.first_name,
        Person.middle_name,
        Person.birth_date,
        Person.phone,
        Person.email,
        Person.address,
        participation_column,
    )

    if event_id is not None:
        stmt = stmt.join(
            EventParticipation,
            sa.and_(
                EventParticipation.person_id == Person.id,
                EventParticipation.event_id == event_id,
            ),
        )
        if participation_status is not None:
            stmt = stmt.where(EventParticipation.registration_status == participation_status)

    if context in ("club", "event"):
        stmt = stmt.where(_club_membership_exists(club_id=club_id, status=membership_status))
    elif context in ("group", "group_event"):
        # GAP-2: the membership record here is GroupMembership only —
        # ClubMembership.status is deliberately not filtered, so e.g. a
        # `suspended` ClubMembership with an `active` GroupMembership stays
        # included (participant-export-api.md §8).
        assert group_id is not None
        stmt = stmt.where(
            _group_membership_exists(club_id=club_id, group_id=group_id, status=membership_status)
        )
    else:
        raise ValueError(f"Unsupported export context: {context!r}")

    stmt = stmt.order_by(
        Person.last_name,
        Person.first_name,
        sa.nulls_first(Person.middle_name),
        Person.id,
    )
    return [
        PersonRow(
            person_id=row.id,
            last_name=row.last_name,
            first_name=row.first_name,
            middle_name=row.middle_name,
            birth_date=row.birth_date,
            phone=row.phone,
            email=row.email,
            address=row.address,
            participation_status=row.participation_status,
        )
        for row in session.execute(stmt).all()
    ]


def guardian_display_name(last_name: str, first_name: str, middle_name: str | None) -> str:
    """GAP-4: `Фамилия Имя Отчество`."""
    return " ".join(part for part in (last_name, first_name, middle_name) if part)


def list_active_guardians(
    session: Session, *, child_person_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[GuardianContact]]:
    """Every currently active GuardianRelationship (status `active`, inside
    its `[valid_from, valid_to)` interval) of each child, in a stable order
    (guardian last/first/middle name, then guardian id). No primary guardian
    exists (ADR-0035 §8): all active representatives are listed equally,
    each guardian Person once."""
    if not child_person_ids:
        return {}
    guardian = aliased(Person)
    now = sa.func.now()
    stmt = (
        sa.select(
            GuardianRelationship.child_person_id,
            guardian.id.label("guardian_person_id"),
            guardian.last_name,
            guardian.first_name,
            guardian.middle_name,
            guardian.phone,
        )
        .join(guardian, guardian.id == GuardianRelationship.guardian_person_id)
        .where(
            GuardianRelationship.child_person_id.in_(child_person_ids),
            GuardianRelationship.status == _ACTIVE_GUARDIAN_RELATIONSHIP_STATUS,
            GuardianRelationship.valid_from <= now,
            sa.or_(GuardianRelationship.valid_to.is_(None), now < GuardianRelationship.valid_to),
        )
        .order_by(
            GuardianRelationship.child_person_id,
            guardian.last_name,
            guardian.first_name,
            sa.nulls_first(guardian.middle_name),
            guardian.id,
        )
    )
    result: dict[uuid.UUID, list[GuardianContact]] = {}
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for row in session.execute(stmt).all():
        # Several active relationships of different `relationship_type`
        # between the same guardian and child list that guardian once.
        key = (row.child_person_id, row.guardian_person_id)
        if key in seen:
            continue
        seen.add(key)
        result.setdefault(row.child_person_id, []).append(
            GuardianContact(
                name=guardian_display_name(row.last_name, row.first_name, row.middle_name),
                phone=row.phone,
            )
        )
    return result


__all__ = [
    "GuardianContact",
    "PersonRow",
    "guardian_display_name",
    "list_active_guardians",
    "list_export_persons",
]
