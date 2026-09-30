"""Canonical Participant Export field allowlist (participant-export-api.md §5).

This registry is the only source of exportable fields. A field that is not
listed here cannot be exported, whatever the client sends — the existence of
an attribute on Person, GuardianRelationship, Event or Document does not by
itself make it exportable (§5, §6). Adding a field requires a PO decision
and a canonical documentation update first.

Every field carries:

- `code` — the stable `field_code` the client selects;
- `label` — the display label used as the column header in every format;
- `contexts` — the export contexts the field is available in (Group fields
  only where a Group is part of the context, Event fields only where an
  Event is);
- the existing read permission its data is governed by (see
  `field_permission`), so selecting a field can never widen the requester's
  existing rights (§2; GAP-1).
"""

from dataclasses import dataclass
from typing import Final, Literal

ExportContext = Literal["club", "group", "event", "group_event"]
ExportFormat = Literal["xlsx", "pdf", "print"]

EXPORT_CONTEXTS: Final[tuple[ExportContext, ...]] = ("club", "group", "event", "group_event")
GROUP_CONTEXTS: Final[frozenset[str]] = frozenset({"group", "group_event"})
EVENT_CONTEXTS: Final[frozenset[str]] = frozenset({"event", "group_event"})
_ALL_CONTEXTS: Final[frozenset[str]] = frozenset(EXPORT_CONTEXTS)

PERSON_READ = "person.read"
MEMBERSHIP_READ = "membership.read"
GROUP_READ = "group.read"
EVENT_READ = "event.read"
GUARDIAN_RELATIONSHIP_READ = "guardian_relationship.read"


@dataclass(frozen=True)
class ExportField:
    code: str
    label: str
    contexts: frozenset[str]


EXPORT_FIELDS: Final[tuple[ExportField, ...]] = (
    # Person
    ExportField("person.last_name", "Фамилия", _ALL_CONTEXTS),
    ExportField("person.first_name", "Имя", _ALL_CONTEXTS),
    ExportField("person.middle_name", "Отчество", _ALL_CONTEXTS),
    ExportField("person.birth_date", "Дата рождения", _ALL_CONTEXTS),
    ExportField("person.phone", "Телефон", _ALL_CONTEXTS),
    ExportField("person.email", "Email", _ALL_CONTEXTS),
    ExportField("person.address", "Адрес", _ALL_CONTEXTS),
    # Group / membership context. `membership.status` is available in every
    # context; which membership record it reads is fixed per context by
    # `membership_status_source` (GAP-2).
    ExportField("group.name", "Группа", GROUP_CONTEXTS),
    ExportField("membership.status", "Статус членства", _ALL_CONTEXTS),
    # Event context
    ExportField("event.name", "Мероприятие", EVENT_CONTEXTS),
    ExportField("event.starts_at", "Начало мероприятия", EVENT_CONTEXTS),
    ExportField("event_participation.status", "Статус участия", EVENT_CONTEXTS),
    # Guardian context — never included implicitly (§5).
    ExportField("guardian.name", "Представитель", _ALL_CONTEXTS),
    ExportField("guardian.phone", "Телефон представителя", _ALL_CONTEXTS),
)

EXPORT_FIELDS_BY_CODE: Final[dict[str, ExportField]] = {f.code: f for f in EXPORT_FIELDS}


def membership_status_source(context: str) -> Literal["club_membership", "group_membership"]:
    """GAP-2: `club`/`event` → ClubMembership.status; `group`/`group_event`
    → GroupMembership.membership_status. The same record is the one the
    `membership_status` filter applies to."""
    return "group_membership" if context in GROUP_CONTEXTS else "club_membership"


def context_permissions(context: str) -> frozenset[str]:
    """Existing read permissions governing the data every export in
    `context` is built from, independent of the selected fields: the Person
    rows themselves, plus the membership/participation records the context
    and its status filter resolve through. GroupMembership is read under
    `group.read` exactly as `GET /groups/{group_id}/members` reads it;
    EventParticipation under `event.read`."""
    permissions = {PERSON_READ}
    if context in GROUP_CONTEXTS:
        permissions.add(GROUP_READ)
    else:
        permissions.add(MEMBERSHIP_READ)
    if context in EVENT_CONTEXTS:
        permissions.add(EVENT_READ)
    return frozenset(permissions)


def field_permission(code: str, context: str) -> str:
    """The existing read permission governing one field's data."""
    if code.startswith("person."):
        return PERSON_READ
    if code == "group.name":
        return GROUP_READ
    if code == "membership.status":
        return (
            GROUP_READ
            if membership_status_source(context) == "group_membership"
            else MEMBERSHIP_READ
        )
    if code.startswith("event.") or code.startswith("event_participation."):
        return EVENT_READ
    if code.startswith("guardian."):
        return GUARDIAN_RELATIONSHIP_READ
    # Unreachable for a registered field; guards a future registry entry
    # added without a permission mapping (fail closed).
    raise ValueError(f"No permission mapping for export field {code!r}")


__all__ = [
    "EVENT_CONTEXTS",
    "EXPORT_CONTEXTS",
    "EXPORT_FIELDS",
    "EXPORT_FIELDS_BY_CODE",
    "ExportContext",
    "ExportField",
    "ExportFormat",
    "GROUP_CONTEXTS",
    "context_permissions",
    "field_permission",
    "membership_status_source",
]
