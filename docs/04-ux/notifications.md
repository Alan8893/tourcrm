# TourCRM — Notifications UX

## 1. Назначение

Defines the MVP UX contract for the global Notification Center. It is a platform surface for configuring delivery channels and notification policies; it does not define individual business-event notification rules.

MVP channels:

- Email;
- Telegram.

MAX, push and SMS are outside this UX contract.

## 2. Administrator surface

Administrator receives a **Settings → Notifications** area with these conceptual sections:

### General

- global notification availability;
- effective channel state;
- safe status/errors.

### Email

- enabled/disabled;
- SMTP host;
- port;
- username;
- TLS/SSL mode;
- sender name;
- sender email;
- test recipient;
- connection/test status.

SMTP password is write-only/secret. After save, the UI must not display the stored value.

### Telegram

- enabled/disabled;
- bot connection/status;
- bot identity/link;
- user-linking status;
- Telegram destinations;
- group/topic routing;
- test message.

Bot token is write-only/secret.

For a Telegram Topic, the UI may display its human-readable name, but the persisted routing identity is the Telegram `message_thread_id`.

### Notification rules

Shows the notification event types and effective channel policy once the event catalog has been separately approved.

The UI must distinguish:

- globally disabled by Administrator;
- disabled by rule;
- disabled by user preference;
- enabled and deliverable.

### Implemented scope (Issue #333, ADR-0048)

- **General** is the per-channel Global Admin Policy (Email on/off, Telegram on/off) plus each channel's readiness; with no saved policy every channel is off.
- **Notification rules** lists the existing installation-wide rules (`club_id = NULL`) with an enable/disable switch only — no create/delete, no event catalog editing; an empty list is a normal state.
- **Email** and **Telegram** edit the non-secret settings; the SMTP password and bot token are write-only fields showing `••••••••` when configured, with separate «Заменить» and «Очистить» (confirmation dialog) actions. Saving the other fields never sends or changes a secret.
- **Test message**: Email to an address entered by the administrator; Telegram to the administrator's own linked account or an existing enabled destination. Results are labelled as test results.
- Telegram destination/topic management, user-linking overview and user preferences remain separate slices.

## 3. User surface

Authenticated users receive **My Notifications / Notification Preferences** in Settings.

For each supported notification type/channel, the user may opt in/out where the effective Admin Policy permits.

If Admin Policy disables a channel or event:

- the user cannot enable it;
- the UI explains that it is disabled by club/system policy.

Telegram linking is explicit and user-driven. The user starts the linking flow from TourCRM, then starts/interacts with the configured Telegram bot using the one-time link.

## 4. Delivery journal

Administrator-facing delivery history is a future implementation surface of the Notification Center.

The journal should expose safe operational information:

- created/scheduled time;
- recipient;
- event type;
- channel;
- status;
- attempt count;
- provider message identifier where safe;
- last safe error.

It must not expose secrets, raw tokens or provider credentials.

## 5. UX states

Channel configuration must distinguish at least:

- not configured;
- configured and disabled;
- configured and enabled;
- configuration/test failed.

Saving an invalid configuration must not leave a partially valid state.

Test-send actions must be explicit and must not silently enable the channel.

## 6. Responsive and role rules

- Notification administration is Administrator-only.
- User preferences are available only to the authenticated user.
- Frontend hiding is not the authorization boundary; backend authorization is authoritative.
- Mobile layout must remain usable without horizontal scrolling.
