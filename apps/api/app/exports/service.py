"""Participant Export — the single entry point producing the canonical,
authorized dataset (participant-export-api.md §2-§8; Issue #218 GAP-1..7).

`build_participant_export` is the only way to obtain export data. It runs,
in order and exactly once per export, whatever the output format:

1. Administrator check in the current Club (403);
2. request validation — context/filter combination, status vocabulary,
   field allowlist and per-context field availability (422);
3. existing read grants with `all` scope for the context's data and for
   every requested field (403);
4. Group/Event resolution inside the current Club — a nonexistent object
   and one of another Club are indistinguishable (404);
5. canonical dataset construction (app.exports.queries).

The output formats (app.exports.rendering) only present the returned
dataset; they never query or authorize anything themselves (§7). The export
never changes Person, Membership, GroupMembership or EventParticipation
(§8) — this module performs reads only.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.events import Event
from app.db.groups import CANONICAL_GROUP_MEMBERSHIP_STATUSES, Group
from app.exports import queries
from app.exports.authorization import require_club_administrator, require_club_wide_read
from app.exports.fields import (
    EVENT_CONTEXTS,
    EXPORT_CONTEXTS,
    EXPORT_FIELDS_BY_CODE,
    GROUP_CONTEXTS,
    ExportField,
    context_permissions,
    field_permission,
    membership_status_source,
)
from app.imports.authorization import resolve_sole_club_id
from app.people.lifecycle import CANONICAL_MEMBERSHIP_STATUSES

# GAP-3: ClubMembership and GroupMembership default to `active`;
# EventParticipation defaults to every status (no filter).
DEFAULT_MEMBERSHIP_STATUS = "active"
# Positional placeholder for a guardian without a phone in `guardian.phone`.
MISSING_GUARDIAN_PHONE = "—"

CellValue = str | date | datetime | None


class ExportRequestError(Exception):
    """An invalid export request — mapped to 422 by the router."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ExportTargetNotFoundError(Exception):
    """The requested Group/Event does not exist or belongs to another
    Club — deliberately one error for both (existence hiding)."""

    def __init__(self, target: str) -> None:
        super().__init__(target)
        self.target = target


@dataclass(frozen=True)
class ParticipantExportRequest:
    context: str
    fields: tuple[str, ...]
    group_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    membership_status: str | None = None
    participation_status: str | None = None


@dataclass(frozen=True)
class ParticipantExportDataset:
    """The one canonical dataset every output format renders."""

    title: str
    generated_at: datetime
    columns: tuple[ExportField, ...]
    rows: tuple[tuple[CellValue, ...], ...]


def guardian_cells(
    contacts: Sequence[queries.GuardianContact],
) -> tuple[str | None, str | None]:
    """`guardian.name` and `guardian.phone` cells (GAP-4) built from the one
    stable guardian list, position by position: the n-th phone always
    belongs to the n-th name. A guardian without a phone keeps its position
    as `MISSING_GUARDIAN_PHONE` rather than being dropped. No guardian →
    both cells empty."""
    if not contacts:
        return None, None
    names = "; ".join(contact.name for contact in contacts)
    phones = "; ".join(contact.phone or MISSING_GUARDIAN_PHONE for contact in contacts)
    return names, phones


def _validate_context_filters(request: ParticipantExportRequest) -> None:
    context = request.context
    if context not in EXPORT_CONTEXTS:
        raise ExportRequestError(
            "invalid_export_context",
            "Unsupported export context",
            {"context": context, "allowed": list(EXPORT_CONTEXTS)},
        )
    in_group_context = context in GROUP_CONTEXTS
    in_event_context = context in EVENT_CONTEXTS
    if in_group_context and request.group_id is None:
        raise ExportRequestError(
            "export_context_target_required",
            "group_id is required for this export context",
            {"context": context, "field": "group_id"},
        )
    if in_event_context and request.event_id is None:
        raise ExportRequestError(
            "export_context_target_required",
            "event_id is required for this export context",
            {"context": context, "field": "event_id"},
        )
    not_applicable = []
    if not in_group_context and request.group_id is not None:
        not_applicable.append("group_id")
    if not in_event_context and request.event_id is not None:
        not_applicable.append("event_id")
    if not in_event_context and request.participation_status is not None:
        not_applicable.append("participation_status")
    if not_applicable:
        raise ExportRequestError(
            "export_filter_not_applicable",
            "One or more filters do not apply to this export context",
            {"context": context, "fields": not_applicable},
        )


def _resolve_membership_status(request: ParticipantExportRequest) -> str:
    status = (
        request.membership_status
        if request.membership_status is not None
        else DEFAULT_MEMBERSHIP_STATUS
    )
    if membership_status_source(request.context) == "group_membership":
        allowed = CANONICAL_GROUP_MEMBERSHIP_STATUSES
    else:
        allowed = CANONICAL_MEMBERSHIP_STATUSES
    if status not in allowed:
        raise ExportRequestError(
            "invalid_membership_status",
            "Unsupported membership_status for this export context",
            {"context": request.context, "allowed": sorted(allowed)},
        )
    return status


def _validate_fields(request: ParticipantExportRequest) -> tuple[ExportField, ...]:
    if not request.fields:
        raise ExportRequestError("empty_export_fields", "At least one export field is required")
    duplicates = sorted({code for code in request.fields if request.fields.count(code) > 1})
    if duplicates:
        raise ExportRequestError(
            "duplicate_export_field", "Export fields must be unique", {"fields": duplicates}
        )
    unknown = [code for code in request.fields if code not in EXPORT_FIELDS_BY_CODE]
    if unknown:
        raise ExportRequestError(
            "unknown_export_field",
            "One or more fields are not in the export-field allowlist",
            {"fields": unknown},
        )
    columns = tuple(EXPORT_FIELDS_BY_CODE[code] for code in request.fields)
    unavailable = [f.code for f in columns if request.context not in f.contexts]
    if unavailable:
        raise ExportRequestError(
            "export_field_not_available",
            "One or more fields are not available in this export context",
            {"context": request.context, "fields": unavailable},
        )
    return columns


def validate_export_request(
    request: ParticipantExportRequest,
) -> tuple[str, tuple[ExportField, ...]]:
    """Context/filter combination, membership status vocabulary and field
    allowlist validation (no database access). Returns the effective
    membership status and the selected columns in request order."""
    _validate_context_filters(request)
    membership_status = _resolve_membership_status(request)
    columns = _validate_fields(request)
    return membership_status, columns


def _resolve_group(session: Session, *, group_id: uuid.UUID, club_id: uuid.UUID) -> Group:
    group = session.get(Group, group_id)
    if group is None or group.club_id != club_id:
        raise ExportTargetNotFoundError("group")
    return group


def _resolve_event(session: Session, *, event_id: uuid.UUID, club_id: uuid.UUID) -> Event:
    event = session.get(Event, event_id)
    if event is None or event.club_id != club_id:
        raise ExportTargetNotFoundError("event")
    return event


def _title(context: str, group: Group | None, event: Event | None) -> str:
    if context == "group" and group is not None:
        return f"Участники группы «{group.name}»"
    if context == "event" and event is not None:
        return f"Участники мероприятия «{event.title}»"
    if context == "group_event" and group is not None and event is not None:
        return f"Участники группы «{group.name}» — мероприятие «{event.title}»"
    return "Участники клуба"


def build_participant_export(
    session: Session,
    *,
    user_id: uuid.UUID,
    request: ParticipantExportRequest,
    now: datetime | None = None,
) -> ParticipantExportDataset:
    club_id = resolve_sole_club_id(session)
    require_club_administrator(session, user_id=user_id, club_id=club_id)

    membership_status, columns = validate_export_request(request)

    required_permissions = set(context_permissions(request.context))
    required_permissions.update(field_permission(f.code, request.context) for f in columns)
    require_club_wide_read(
        session, user_id=user_id, club_id=club_id, permission_codes=required_permissions
    )

    group = (
        _resolve_group(session, group_id=request.group_id, club_id=club_id)
        if request.group_id is not None
        else None
    )
    event = (
        _resolve_event(session, event_id=request.event_id, club_id=club_id)
        if request.event_id is not None
        else None
    )

    persons = queries.list_export_persons(
        session,
        context=request.context,
        club_id=club_id,
        group_id=group.id if group is not None else None,
        event_id=event.id if event is not None else None,
        membership_status=membership_status,
        participation_status=request.participation_status,
    )
    selected = {f.code for f in columns}
    guardians = (
        queries.list_active_guardians(session, child_person_ids=[p.person_id for p in persons])
        if selected & {"guardian.name", "guardian.phone"}
        else {}
    )
    event_starts_at = (
        event.start_at.astimezone(ZoneInfo(event.timezone)) if event is not None else None
    )

    def cell(code: str, person: queries.PersonRow) -> CellValue:
        if code == "person.last_name":
            return person.last_name
        if code == "person.first_name":
            return person.first_name
        if code == "person.middle_name":
            return person.middle_name
        if code == "person.birth_date":
            return person.birth_date
        if code == "person.phone":
            return person.phone
        if code == "person.email":
            return person.email
        if code == "person.address":
            return person.address
        if code == "group.name":
            return group.name if group is not None else None
        if code == "membership.status":
            # Every row matched the membership predicate for exactly this
            # status, so the value is the same for all rows (GAP-2/GAP-3).
            return membership_status
        if code == "event.name":
            return event.title if event is not None else None
        if code == "event.starts_at":
            return event_starts_at
        if code == "event_participation.status":
            return person.participation_status
        if code == "guardian.name":
            return guardian_cells(guardians.get(person.person_id, []))[0]
        if code == "guardian.phone":
            return guardian_cells(guardians.get(person.person_id, []))[1]
        raise ValueError(f"Unhandled export field {code!r}")

    rows = tuple(tuple(cell(f.code, person) for f in columns) for person in persons)
    return ParticipantExportDataset(
        title=_title(request.context, group, event),
        generated_at=now if now is not None else datetime.now(timezone.utc),
        columns=columns,
        rows=rows,
    )


__all__ = [
    "DEFAULT_MEMBERSHIP_STATUS",
    "MISSING_GUARDIAN_PHONE",
    "CellValue",
    "ExportRequestError",
    "ExportTargetNotFoundError",
    "ParticipantExportDataset",
    "ParticipantExportRequest",
    "build_participant_export",
    "guardian_cells",
    "validate_export_request",
]
