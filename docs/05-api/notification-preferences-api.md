# Personal Notification Preferences API

- **Status:** Implemented (Issue #336, PR-0)
- **Architecture:** ADR-0049 §2.1/§2.2 (business-event notification integration), ADR-0045 §2.4/§2.8
- **Code:** `apps/api/app/api/v1/notification_preferences.py`, `apps/api/app/notifications/preferences.py`

The authenticated User controls **their own personal Telegram messages** for the business notification catalog (`docs/04-modules/notification-event-catalog.md`):

- a **personal master switch** — "personal Telegram messages";
- a **per-event preference** for every optional catalog type.

An optional notification is planned for personal delivery only when the master switch **and** the event preference are ON. An unset value is OFF. Switching the master switch never changes a per-event value, so switching it back on restores the earlier choices.

`event.cancelled` and `event.rescheduled` are mandatory: the personal opt-out does not apply to them. They are listed read-only. `membership.approved` is blocked (no canonical workflow) and is not listed.

These preferences govern **personal messages only** (`destination_type = user`). Messages to the club's Telegram group or its topics are configured by an administrator and are never affected by them. Nothing here can enable what the administrator disabled: Global Telegram OFF and a disabled rule block delivery, mandatory types included (ADR-0045 §2.4).

Personal delivery also requires a verified linked Telegram account (`docs/05-api/telegram-link-api.md`). Without one nothing is sent, and the response reports `telegram_linked: false` so the UI can offer the linking flow.

Both endpoints are self-scoped (like `/me/telegram-link`): the identity is the session-derived principal, no RBAC permission is involved, and no request field can name another User. The backend enforces the values when it plans each delivery; hiding a control in the UI is not the authorization boundary.

## `GET /api/v1/me/notification-preferences`

```json
{
  "channel": "telegram",
  "personal_enabled": false,
  "telegram_linked": true,
  "events": [
    { "event_type": "event.created", "mandatory": false, "enabled": false },
    { "event_type": "event.updated", "mandatory": false, "enabled": false },
    { "event_type": "event.cancelled", "mandatory": true, "enabled": true },
    { "event_type": "event.rescheduled", "mandatory": true, "enabled": true },
    { "event_type": "registration.created", "mandatory": false, "enabled": false },
    { "event_type": "registration.cancelled", "mandatory": false, "enabled": false },
    { "event_type": "attendance.changed", "mandatory": false, "enabled": false },
    { "event_type": "news.published", "mandatory": false, "enabled": true },
    { "event_type": "achievement.awarded", "mandatory": false, "enabled": false }
  ]
}
```

`enabled` of an optional type is the stored per-event value (unset = `false`). It is reported independently of `personal_enabled`.

Errors: `401` unauthenticated.

## `PUT /api/v1/me/notification-preferences`

Partial update. Requires the CSRF header (`X-CSRF-Token`). An omitted field leaves the stored value unchanged. Values must be JSON booleans; unknown fields are rejected.

```json
{ "personal_enabled": true, "events": { "news.published": true, "event.created": false } }
```

`200 OK` with the same body as `GET`.

| Status | Code | When |
|---|---|---|
| 401 | — | unauthenticated |
| 403 | — | missing/invalid CSRF token |
| 422 | `invalid_notification_type` | `events` names a mandatory, blocked or unknown type; nothing is changed |
| 422 | validation error | malformed body (non-boolean value, unknown field) |
