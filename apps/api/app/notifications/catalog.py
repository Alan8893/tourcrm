"""The v1 business notification catalog in code (Issue #336, ADR-0049 §2.8;
docs/04-modules/notification-event-catalog.md).

Pure Python — no ORM import. Exactly the ten approved catalog keys, each
with the parts of its specification gate the Engine needs:

- `mandatory` — `event.cancelled` / `event.rescheduled` ignore the personal
  opt-out (catalog §5); every other key is personal opt-in: both the
  personal master switch and the per-event preference must be ON, a
  missing value is OFF (ADR-0049 §2.1);
- `status` — `pending` until its vertical slice integrates the domain
  service; `implemented` afterwards; `blocked` when no canonical domain
  workflow exists (`membership.approved`, PO decision 12). Only an
  `implemented` key can be planned: app.notifications.business refuses
  `pending` and `blocked` keys before any write;
- `recipient_scope` — the `notification_rules.recipient_scope` of its
  personal rule; a group/topic rule uses RECIPIENT_SCOPE_TELEGRAM_
  DESTINATION;
- `group_routing` — whether an administrator-configured group/topic route
  may publish it (PO decision 7: `event.created`, `event.cancelled`,
  `event.rescheduled`, and `news.published` when the news audience is
  `club` — that audience condition is checked by its slice);
- the Telegram template code and its declared variables (catalog §4).
  Only Telegram: no business key has an Email template (catalog §1.2).

Template rows and disabled rules are seeded by each key's slice, never
here; a rule is enabled only by an administrator (PO decision 13).
"""

from dataclasses import dataclass
from typing import Literal

from app.notifications.rendering import LinkSpec, TemplateVariables

CatalogStatus = Literal["pending", "implemented", "blocked"]

STATUS_PENDING: CatalogStatus = "pending"
STATUS_IMPLEMENTED: CatalogStatus = "implemented"
STATUS_BLOCKED: CatalogStatus = "blocked"

EVENT_CREATED = "event.created"
EVENT_UPDATED = "event.updated"
EVENT_CANCELLED = "event.cancelled"
EVENT_RESCHEDULED = "event.rescheduled"
REGISTRATION_CREATED = "registration.created"
REGISTRATION_CANCELLED = "registration.cancelled"
ATTENDANCE_CHANGED = "attendance.changed"
NEWS_PUBLISHED = "news.published"
ACHIEVEMENT_AWARDED = "achievement.awarded"
MEMBERSHIP_APPROVED = "membership.approved"

# Personal audience scopes (`notification_rules.recipient_scope`).
SCOPE_TARGETED_GROUP_MEMBERS = "targeted_group_members"
SCOPE_REGISTERED_PARTICIPANTS = "registered_participants"
SCOPE_REGISTRANT = "registrant"
SCOPE_ATTENDANCE_PARTICIPANT = "attendance_participant"
SCOPE_NEWS_AUDIENCE = "news_audience"
SCOPE_AWARD_RECIPIENT = "award_recipient"
SCOPE_APPLICANT = "applicant"

# Fixed frontend pages (apps/web/src/App.tsx): the Events deep link, the
# News detail page and the Achievements section.
EVENT_LINK = LinkSpec(path="/events?event={id}", id_variable="event_id")
NEWS_LINK = LinkSpec(path="/news/{id}", id_variable="news_id")
ACHIEVEMENTS_LINK = LinkSpec(path="/achievements")


@dataclass(frozen=True)
class CatalogEntry:
    event_type: str
    mandatory: bool
    status: CatalogStatus
    recipient_scope: str
    group_routing: bool
    template_code: str
    variables: TemplateVariables


def _entry(
    event_type: str,
    *,
    template_code: str,
    recipient_scope: str,
    variables: TemplateVariables,
    mandatory: bool = False,
    group_routing: bool = False,
    status: CatalogStatus = STATUS_PENDING,
) -> CatalogEntry:
    return CatalogEntry(
        event_type=event_type,
        mandatory=mandatory,
        status=status,
        recipient_scope=recipient_scope,
        group_routing=group_routing,
        template_code=template_code,
        variables=variables,
    )


CATALOG: dict[str, CatalogEntry] = {
    entry.event_type: entry
    for entry in (
        _entry(
            EVENT_CREATED,
            template_code="event_created.telegram",
            recipient_scope=SCOPE_TARGETED_GROUP_MEMBERS,
            group_routing=True,
            variables=TemplateVariables(
                required=frozenset({"event_title", "event_datetime"}),
                links={"event_url": EVENT_LINK},
            ),
        ),
        _entry(
            EVENT_UPDATED,
            template_code="event_updated.telegram",
            recipient_scope=SCOPE_REGISTERED_PARTICIPANTS,
            variables=TemplateVariables(
                required=frozenset({"event_title"}),
                # Catalog §4: a safe generic message when no summary exists.
                optional={"change_summary": "изменены сведения о событии"},
                links={"event_url": EVENT_LINK},
            ),
        ),
        _entry(
            EVENT_CANCELLED,
            template_code="event_cancelled.telegram",
            recipient_scope=SCOPE_REGISTERED_PARTICIPANTS,
            mandatory=True,
            group_routing=True,
            variables=TemplateVariables(
                required=frozenset({"event_title", "event_datetime"}),
                optional={"cancellation_reason": "не указана"},
                links={"event_url": EVENT_LINK},
            ),
        ),
        _entry(
            EVENT_RESCHEDULED,
            template_code="event_rescheduled.telegram",
            recipient_scope=SCOPE_REGISTERED_PARTICIPANTS,
            mandatory=True,
            group_routing=True,
            variables=TemplateVariables(
                required=frozenset({"event_title", "old_event_datetime", "new_event_datetime"}),
                links={"event_url": EVENT_LINK},
            ),
        ),
        _entry(
            REGISTRATION_CREATED,
            template_code="registration_created.telegram",
            recipient_scope=SCOPE_REGISTRANT,
            variables=TemplateVariables(
                required=frozenset({"event_title", "event_datetime"}),
                links={"event_url": EVENT_LINK},
            ),
        ),
        _entry(
            REGISTRATION_CANCELLED,
            template_code="registration_cancelled.telegram",
            recipient_scope=SCOPE_REGISTRANT,
            variables=TemplateVariables(
                required=frozenset({"event_title"}),
                links={"event_url": EVENT_LINK},
            ),
        ),
        _entry(
            ATTENDANCE_CHANGED,
            template_code="attendance_changed.telegram",
            recipient_scope=SCOPE_ATTENDANCE_PARTICIPANT,
            variables=TemplateVariables(
                required=frozenset({"event_title", "attendance_status"}),
                links={"event_url": EVENT_LINK},
            ),
        ),
        _entry(
            NEWS_PUBLISHED,
            template_code="news_published.telegram",
            recipient_scope=SCOPE_NEWS_AUDIENCE,
            group_routing=True,
            variables=TemplateVariables(
                required=frozenset({"news_title"}),
                # Catalog §4: an empty excerpt is omitted.
                optional={"news_excerpt": ""},
                links={"news_url": NEWS_LINK},
            ),
        ),
        _entry(
            ACHIEVEMENT_AWARDED,
            template_code="achievement_awarded.telegram",
            recipient_scope=SCOPE_AWARD_RECIPIENT,
            variables=TemplateVariables(
                required=frozenset({"achievement_title"}),
                links={"achievement_url": ACHIEVEMENTS_LINK},
            ),
        ),
        _entry(
            MEMBERSHIP_APPROVED,
            template_code="membership_approved.telegram",
            recipient_scope=SCOPE_APPLICANT,
            # PO decision 9: no link for membership notifications.
            variables=TemplateVariables(),
            status=STATUS_BLOCKED,
        ),
    )
}

_BY_TEMPLATE_CODE: dict[str, CatalogEntry] = {
    entry.template_code: entry for entry in CATALOG.values()
}

# A template outside the catalog (e.g. a plain-text template without
# placeholders) declares no variables: any placeholder in it fails.
NO_VARIABLES = TemplateVariables()


def template_variables(template_code: str) -> TemplateVariables:
    entry = _BY_TEMPLATE_CODE.get(template_code)
    return entry.variables if entry is not None else NO_VARIABLES


def personal_preference_event_types() -> tuple[str, ...]:
    """The catalog keys a user can switch personally: optional and not
    blocked (mandatory keys ignore personal opt-out, catalog §5)."""
    return tuple(
        key
        for key, entry in CATALOG.items()
        if not entry.mandatory and entry.status != STATUS_BLOCKED
    )


def mandatory_event_types() -> tuple[str, ...]:
    return tuple(key for key, entry in CATALOG.items() if entry.mandatory)


__all__ = [
    "STATUS_PENDING",
    "STATUS_IMPLEMENTED",
    "STATUS_BLOCKED",
    "EVENT_CREATED",
    "EVENT_UPDATED",
    "EVENT_CANCELLED",
    "EVENT_RESCHEDULED",
    "REGISTRATION_CREATED",
    "REGISTRATION_CANCELLED",
    "ATTENDANCE_CHANGED",
    "NEWS_PUBLISHED",
    "ACHIEVEMENT_AWARDED",
    "MEMBERSHIP_APPROVED",
    "SCOPE_TARGETED_GROUP_MEMBERS",
    "SCOPE_REGISTERED_PARTICIPANTS",
    "SCOPE_REGISTRANT",
    "SCOPE_ATTENDANCE_PARTICIPANT",
    "SCOPE_NEWS_AUDIENCE",
    "SCOPE_AWARD_RECIPIENT",
    "SCOPE_APPLICANT",
    "CatalogEntry",
    "CATALOG",
    "NO_VARIABLES",
    "template_variables",
    "personal_preference_event_types",
    "mandatory_event_types",
]
