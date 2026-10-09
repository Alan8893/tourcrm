"""`/api/v1/me/notification-preferences` — the authenticated User's own
personal Telegram notification preferences (Issue #336, ADR-0049 §2.1).

Self-scoped like `/me/telegram-link`: the only identity input is the
session-derived `CurrentPrincipal`, so no RBAC permission is involved and
no request field can name another User.

- `GET` — the personal master switch, the per-event preferences (optional
  catalog types; mandatory types listed read-only) and whether a verified
  Telegram account is linked. Unset values are reported as OFF.
- `PUT` — partial update of the master switch and/or per-event
  preferences. Toggling the master switch never changes a per-event value.
  A mandatory, blocked or unknown type -> 422 `invalid_notification_type`,
  nothing changed.

These preferences govern personal messages only; group/topic publication
is administrator configuration and never reads them. The backend enforces
them when planning each delivery (app.notifications.preferences).
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.v1.notification_preferences_schemas import (
    EventPreferenceOut,
    NotificationPreferencesOut,
    NotificationPreferencesUpdate,
)
from app.db.session import get_db
from app.notifications import preferences
from app.telegram import linking

router = APIRouter(prefix="/me/notification-preferences", tags=["me"])


def _out(db: Session, principal: CurrentPrincipal) -> NotificationPreferencesOut:
    stored = preferences.get_personal_preferences(db, user_id=principal.user_id)
    return NotificationPreferencesOut(
        channel=stored.channel,
        personal_enabled=stored.personal_enabled,
        telegram_linked=linking.get_link_status(db, user_id=principal.user_id).linked,
        events=[
            EventPreferenceOut(event_type=e.event_type, mandatory=e.mandatory, enabled=e.enabled)
            for e in stored.events
        ],
    )


@router.get("", response_model=NotificationPreferencesOut)
def get_my_notification_preferences(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> NotificationPreferencesOut:
    return _out(db, principal)


@router.put("", response_model=NotificationPreferencesOut)
def update_my_notification_preferences(
    body: NotificationPreferencesUpdate,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> NotificationPreferencesOut:
    try:
        preferences.set_personal_preferences(
            db,
            user_id=principal.user_id,
            personal_enabled=body.personal_enabled,
            events=body.events,
        )
        db.commit()
    except preferences.UnknownPreferenceEventTypeError as exc:
        db.rollback()
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_notification_type",
            "This notification type cannot be changed",
        ) from exc
    except Exception:
        db.rollback()
        raise
    return _out(db, principal)
