"""The single integration boundary domain services use to plan a catalog
business notification (Issue #336, ADR-0049 §2.6/§2.8/§2.9).

`plan_catalog_notification` is a thin adapter over the existing
Notification Engine — not a second engine. It turns one committed-to-be
business fact into a `NotificationRequest` exactly per the catalog:

- status: only a key whose vertical slice is implemented
  (STATUS_IMPLEMENTED) can be planned; `pending` and `blocked` keys are
  refused before anything is written;
- channel: Telegram only (catalog §1.2) — Email is never a candidate;
- personal recipients: the canonical audience the domain service resolved,
  their RecipientAccess check, the entry's preference policy (mandatory /
  personal opt-in) and the linked-identity reachability check;
- group/topic routes: every enabled route subscribed to the key, only for
  keys whose `group_routing` is allowed (PO decision 7) and only when the
  domain service says the fact may be published to the group
  (`publish_to_routes`, e.g. a `club`-audience News item);
- idempotency keys `<event_type>:<fact_id>:user:<user_id>` and
  `<event_type>:<fact_id>:dest:<destination_id>` (ADR-0049 §2.9) — one
  fact, one Notification per recipient/route, however often it is planned;
- `render_context` is validated against the template's declared variables
  before anything is written.

Transaction contract (ADR-0049 §2.6, PO decision 14): call it inside the
business mutation's own transaction, before that transaction commits, and
never inside a savepoint. It never commits. Any error — a contract error
raised here or any Engine/database failure — must propagate so the whole
business transaction rolls back: neither the business change nor its
notification is lost silently. Policy exclusions (Global OFF, no or a
disabled rule, an inactive template, preference OFF, no linked identity)
are not errors: the business change commits with no Notification.
"""

import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.notifications import TelegramDestination
from app.notification_settings.policy import GlobalAdminPolicy
from app.notifications.catalog import CATALOG, STATUS_IMPLEMENTED, CatalogEntry
from app.notifications.engine import (
    EXCLUDED_TEMPLATE_UNAVAILABLE,
    DestinationRecipient,
    InvalidNotificationRequestError,
    NotificationPlanOutcome,
    NotificationRequest,
    Recipient,
    plan_notifications,
)
from app.notifications.ports import PreferencePolicy, RecipientAccess
from app.notifications.preferences import (
    MandatoryPreferencePolicy,
    PersonalOptInPreferencePolicy,
    TelegramIdentityReachability,
)
from app.notifications.vocabulary import CHANNEL_TELEGRAM

logger = logging.getLogger(__name__)

_MAX_FACT_ID_LENGTH = 120


class CatalogNotificationError(InvalidNotificationRequestError):
    """A catalog contract violation by the calling domain service. Raised
    before anything is written; the caller's transaction must roll back."""


def _entry(event_type: str) -> CatalogEntry:
    """The catalog entry, only once its vertical slice is implemented
    (catalog STATUS_IMPLEMENTED). A `pending` key (slice not yet done) and a
    `blocked` key (no canonical workflow) are refused before any write."""
    entry = CATALOG.get(event_type)
    if entry is None:
        raise CatalogNotificationError(f"{event_type!r} is not a catalog notification type")
    if entry.status != STATUS_IMPLEMENTED:
        raise CatalogNotificationError(
            f"{event_type!r} is {entry.status} and cannot be planned"
        )
    return entry


def _check_context(entry: CatalogEntry, render_context: Mapping[str, str]) -> None:
    variables = entry.variables
    unknown = set(render_context) - variables.context_keys
    if unknown:
        raise CatalogNotificationError(
            f"undeclared render_context variables for {entry.event_type}: {sorted(unknown)}"
        )
    missing = [
        name
        for name in sorted(variables.required_context_keys)
        if not isinstance(render_context.get(name), str) or not render_context[name].strip()
    ]
    if missing:
        raise CatalogNotificationError(
            f"missing render_context variables for {entry.event_type}: {missing}"
        )


def subscribed_destination_ids(session: Session, event_type: str) -> list[uuid.UUID]:
    """Enabled group/topic routes whose `notification_scope.event_types`
    lists `event_type`, in a deterministic order."""
    return list(
        session.execute(
            sa.select(TelegramDestination.id)
            .where(
                TelegramDestination.enabled.is_(True),
                TelegramDestination.notification_scope["event_types"].contains([event_type]),
            )
            .order_by(TelegramDestination.id)
        ).scalars()
    )


def idempotency_key(
    event_type: str,
    fact_id: str,
    *,
    user_id: Optional[uuid.UUID] = None,
    destination_id: Optional[uuid.UUID] = None,
) -> str:
    if (user_id is None) == (destination_id is None):
        raise CatalogNotificationError("exactly one of user_id and destination_id is required")
    if user_id is not None:
        return f"{event_type}:{fact_id}:user:{user_id}"
    return f"{event_type}:{fact_id}:dest:{destination_id}"


def plan_catalog_notification(
    session: Session,
    *,
    event_type: str,
    fact_id: str,
    subject_type: str,
    subject_id: Optional[uuid.UUID],
    club_id: Optional[uuid.UUID],
    recipient_user_ids: Sequence[uuid.UUID],
    recipient_access: RecipientAccess,
    render_context: Mapping[str, str],
    publish_to_routes: bool = False,
) -> NotificationPlanOutcome:
    """Plan one business fact's notifications inside the caller's open
    business transaction. See the module docstring for the contract."""
    entry = _entry(event_type)
    fact = fact_id.strip()
    if not fact or ":" in fact or len(fact) > _MAX_FACT_ID_LENGTH:
        raise CatalogNotificationError("fact_id must be a non-blank id without ':'")
    if publish_to_routes and not entry.group_routing:
        raise CatalogNotificationError(f"{event_type!r} may not be published to group routes")
    _check_context(entry, render_context)

    # Deduplicated in a stable order; the canonical audience is the caller's.
    user_ids = list(dict.fromkeys(recipient_user_ids))
    destination_ids = subscribed_destination_ids(session, event_type) if publish_to_routes else []
    preference_policy: PreferencePolicy = (
        MandatoryPreferencePolicy() if entry.mandatory else PersonalOptInPreferencePolicy()
    )
    request = NotificationRequest(
        event_type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        club_id=club_id,
        recipient_scope=entry.recipient_scope,
        recipients=[
            Recipient(
                user_id=user_id,
                idempotency_key=idempotency_key(event_type, fact, user_id=user_id),
            )
            for user_id in user_ids
        ],
        destinations=[
            DestinationRecipient(
                destination_id=destination_id,
                idempotency_key=idempotency_key(event_type, fact, destination_id=destination_id),
            )
            for destination_id in destination_ids
        ],
        channel_templates={CHANNEL_TELEGRAM: entry.template_code},
        render_context=dict(render_context),
    )
    outcome = plan_notifications(
        session,
        request,
        admin_policy=GlobalAdminPolicy(),
        recipient_access=recipient_access,
        preference_policy=preference_policy,
        reachability=TelegramIdentityReachability(),
    )
    if EXCLUDED_TEMPLATE_UNAVAILABLE in outcome.excluded_channels.values() or any(
        d.excluded_reason == EXCLUDED_TEMPLATE_UNAVAILABLE for d in outcome.destinations
    ):
        # Catalog §2: a missing template creates no Delivery and is a
        # diagnosable condition. Safe fields only.
        logger.warning(
            "notifications.template_unavailable event_type=%s template_code=%s",
            event_type,
            entry.template_code,
        )
    return outcome


__all__ = [
    "CatalogNotificationError",
    "subscribed_destination_ids",
    "idempotency_key",
    "plan_catalog_notification",
]
