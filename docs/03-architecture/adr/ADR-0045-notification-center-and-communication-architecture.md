# ADR-0045 — Notification Center and Communication Architecture

- **Status:** Accepted
- **Date:** 2026-10-08
- **Decision owner:** Product Owner / CTO
- **Decision type:** Architecture decision
- **Supersedes:** none
- **Refines:** ADR-0007 (background processing) for asynchronous notification delivery
- **Related:** ADR-0044, ODR-005, `docs/03-architecture/application-architecture.md`, `docs/03-architecture/database-schema.md`, `docs/09-governance/feature-settings.md`

## 1. Context

TourCRM already anticipates a Notifications module, notification templates/rules/preferences, system settings for `email.enabled` and `telegram.enabled`, and background processing for notification delivery. Authentication already creates email-verification and password-reset challenges, but delivery is not implemented.

News explicitly defers email/Telegram delivery to the future Notifications domain. Therefore notification delivery must be introduced as one shared platform capability rather than as independent integrations inside Auth, News or other business modules.

The architecture must support Email and Telegram in the MVP and leave room for future channels without coupling business modules to provider APIs.

## 2. Decision

TourCRM introduces a **Notification Center** as a shared application module.

The canonical flow is:

```text
Business Event
    ↓
Notification Engine
    ↓
Policy → Audience → Template → Timing
    ↓
Notification
    ↓
Delivery
    ↓
Channel Adapter
 ├── Email
 └── Telegram
```

Business modules emit domain/application events or invoke the Notification Engine through its application boundary. They must not call SMTP or Telegram APIs directly.

### 2.1 Notification

A `Notification` is the logical message addressed to one recipient.

It contains the business event reference, recipient, selected template/rendering context, priority/scheduling state and an idempotency key.

A Notification is not a provider delivery attempt.

### 2.2 Delivery

A `Delivery` represents one channel-specific delivery of a Notification.

One Notification may have multiple Deliveries:

```text
Notification #123
 ├─ Email Delivery → sent
 └─ Telegram Delivery → failed → retry
```

Delivery owns provider-specific state such as attempts, provider message id and last error.

### 2.3 Outbox

Creation of a Notification and its outbox record occurs in the same PostgreSQL transaction as the business change that caused the notification.

The transaction is:

```text
business mutation
+
notification
+
outbox
↓
COMMIT
```

After commit, a background worker processes the outbox and performs channel delivery.

The worker is asynchronous; SMTP/Telegram calls must not execute inline in the HTTP request that commits the business mutation.

The exact queue/worker technology remains governed by ODR-005 and is not selected by this ADR.

### 2.4 Policy precedence

Notification eligibility is resolved in this order:

```text
Admin Policy
    ↓
Notification Rule
    ↓
User Preference
    ↓
Delivery
```

An administrator-level disabled channel or notification rule is authoritative.

**Admin OFF always overrides User ON.**

A user preference can opt out only where the effective administrative policy permits the notification.

Security authorization remains independent from notification settings. Settings never grant access to a resource or permission.

### 2.5 MVP channels

MVP channels:

- Email;
- Telegram.

MAX remains an architectural extension point only. No MAX implementation is introduced by this ADR.

Each channel is implemented behind a channel adapter interface. Provider-specific APIs and credentials remain inside the adapter/integration boundary.

### 2.6 Email

Email delivery uses SMTP.

SMTP connection details and credentials are integration/deployment configuration, not ordinary user feature settings.

Secrets:

- are never returned after save;
- are never displayed in normal UI after save;
- are never written to ordinary logs or notification payloads;
- are supplied through environment-specific secret management where appropriate.

The Notification Center provides a safe test-send operation for an authorized administrator.

### 2.7 Telegram identity and destinations

A user links their own Telegram identity through a one-time linking flow. Manual entry of a Telegram numeric id is not the canonical UX.

The Telegram bot cannot initiate a private conversation with a user who has not first interacted with the bot; therefore linking is explicitly user-driven.

For group delivery, TourCRM uses a Telegram Destination:

- `chat_id` — identifies the Telegram chat/group;
- `message_thread_id` nullable — identifies a Topic when Telegram topics are used;
- `name`;
- `enabled`;
- notification scope/configuration.

A Topic is identified by `message_thread_id`, not by its display name.

### 2.8 Preferences

User preferences are channel/event preferences and are subordinate to effective administrative policy.

The preference model must not allow a user to enable a channel that the administrator has globally disabled.

Quiet hours may be supported by channel where defined by the future UX/API contract; this ADR does not define a universal quiet-hours policy.

### 2.9 Reliability

Notification delivery is retry-safe and idempotent.

Delivery state must be persisted, including:

- status;
- attempt count;
- first/last attempt timestamps;
- next retry time where applicable;
- provider message id;
- last safe error code/message.

A failed delivery must not roll back the already committed business transaction.

A terminal delivery failure remains observable in the delivery journal.

## 3. Data model contract

The canonical relational model is:

### `notification_templates`

- `id` PK
- `code` unique
- `channel`
- `locale`
- `subject_template` nullable
- `body_template`
- `version`
- `is_active`
- timestamps

### `notification_rules`

- `id` PK
- `club_id` nullable
- `event_type`
- `channel`
- `recipient_scope`
- `is_enabled`
- scheduling parameters
- timestamps

### `notifications`

- `id` PK
- `club_id` nullable
- `event_type`
- `subject_type`
- `subject_id` nullable
- `recipient_user_id`
- `template_id` nullable
- `priority`
- `scheduled_at` nullable
- `status`
- `idempotency_key`
- timestamps

### `notification_deliveries`

- `id` PK
- `notification_id` FK
- `channel`
- `destination_type`
- `destination_id`
- `status`
- `attempts`
- `first_attempt_at` nullable
- `delivered_at` nullable
- `next_retry_at` nullable
- `provider_message_id` nullable
- `last_error_code` nullable
- `last_error_message` nullable
- timestamps

### `communication_preferences`

- `id` PK
- `user_id` FK
- `channel`
- `notification_type`
- `enabled`
- quiet-hours configuration where applicable
- timestamps

### Telegram destinations

Telegram group/topic routing is represented as a destination configuration associated with the integration boundary. The implementation must preserve `chat_id` and optional `message_thread_id`; topic names are presentation metadata only.

The exact physical table name and external-identity association are implementation details of the DB/API task, but the above invariants are normative.

## 4. Security and authorization

- Backend authorization remains authoritative.
- Notification visibility must not bypass the recipient's existing resource authorization.
- Channel settings never grant permissions.
- Provider credentials and bot tokens are secrets.
- Raw verification/reset/linking tokens are never persisted or logged.
- Notification payloads and delivery errors must not contain passwords, tokens or other credentials.
- Administrative configuration changes follow the existing settings/audit policy.

## 5. Business-event integration boundary

The following are candidate integrations, **not automatic MVP event rules**:

- authentication email verification/password reset;
- Event created/updated/cancelled/rescheduled;
- lesson created/cancelled;
- registration created/cancelled;
- attendance changed;
- News published;
- achievement awarded;
- membership approved.

Each business event must pass its own specification gate defining:

1. recipient audience;
2. allowed channels;
3. mandatory vs opt-in delivery;
4. template;
5. timing;
6. retry/expiry behaviour;
7. quiet-hours behaviour where relevant.

No business module may invent these rules locally.

## 6. Consequences

### Positive

- One delivery architecture for Email and Telegram.
- Business modules remain independent of provider APIs.
- A single notification can have multiple channel deliveries.
- Transactional outbox prevents committed business changes from losing notifications because of provider failure.
- Delivery failures become observable and retryable.
- Future channels can be added as adapters.

### Negative / limitations

- Notification delivery becomes a background-processing dependency.
- A worker/outbox implementation is required before reliable asynchronous delivery is production-ready.
- Admin policy, rules and user preferences require explicit API/UI contracts.
- Telegram linking and group/topic routing add integration state beyond the User profile.
- The exact worker technology remains deferred under ODR-005.

## 7. Non-decisions

This ADR does not decide:

- the queue/worker framework;
- the exact retry/backoff schedule;
- the complete business event catalog;
- marketing/bulk messaging;
- push/SMS;
- MAX implementation;
- a visual template editor;
- production deployment topology.

Those require separate decisions/specification gates where necessary.

## 8. Implementation order

1. Reconcile canonical documentation with this ADR.
2. Define the Notification/Delivery/Outbox persistence contract.
3. Implement Notification Engine and policy resolution.
4. Implement asynchronous outbox worker according to the approved worker decision.
5. Implement Email adapter.
6. Implement Telegram adapter and user linking.
7. Implement administrator notification settings.
8. Implement user preferences.
9. Integrate individual business events through separate specification gates.

