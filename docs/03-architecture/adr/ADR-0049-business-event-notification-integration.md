# ADR-0049 — Business-Event Notification Integration

- **Status:** Accepted
- **Date:** 2026-10-09
- **Decision owner:** Product Owner / CTO
- **Decision type:** Architecture decision
- **Refines:** ADR-0045 (Notification Center and Communication Architecture), ADR-0048 (Administrator Notification Settings)
- **Related:** Issue #336, `docs/04-modules/notification-event-catalog.md`, `docs/04-ux/notifications.md`, ADR-0046 (PostgreSQL-backed worker), ADR-0047 (Telegram long polling and linking)

## 1. Context

The Notification Engine, the transactional outbox worker, the Email/Telegram adapters, Telegram user linking and the Administrator Notification Settings exist (ADR-0045–ADR-0048). The approved v1 business notification catalog (ten event types, Telegram only) and the Product Owner decisions recorded on Issue #336 now have to be connected to the domain services without creating a second notification mechanism.

The audit for Issue #336 found that the existing model could not express the approved contract:

1. `communication_preferences` is unique per (`user_id`, `channel`, `notification_type`) and has no destination dimension. It cannot express "personal Telegram messages off" while leaving group/topic publication unchanged, and it has no master switch that preserves per-event choices (PO decision, option C).
2. A `Notification` stores only a `template_id`. There is no rendering context, so a value that belongs to the moment of the business change (the old date of a rescheduled event) cannot be reconstructed at send time. The Telegram adapter sent `body_template` literally, so `{{placeholders}}` would reach users unrendered.
3. `notifications.recipient_user_id` is NOT NULL and the Engine created only `destination_type = user` Deliveries, so a group/topic publication could not be represented.
4. Domain services own their commits. A post-commit Engine call would open a loss window between the business commit and the notification (catalog §2, Issue #336).
5. The catalog text (§1.6) still said notifications are created "after a successful commit". This contradicted the atomic model of §2/§6 and of Issue #336.

## 2. Decision

### 2.1 Personal preferences are a destination of their own

User preferences govern only the **personal** destination (`destination_type = user`). Group/topic routes are administrator configuration and never read a user preference. A user preference never affects a route.

- `communication_preferences` gains `destination_type` (NOT NULL, default `user`, CHECK `IN ('user')`). Its UNIQUE constraint becomes (`user_id`, `channel`, `destination_type`, `notification_type`). Existing rows are personal preferences. A future destination dimension is a vocabulary + CHECK migration, not a redesign.
- New table `communication_channel_preferences` (`user_id`, `channel`, `destination_type`, `enabled`; UNIQUE per user/channel/destination) holds the **personal master switch**. It is stored apart from the per-event rows, so toggling it never rewrites them; switching it back on restores the user's earlier choices.
- Effective personal policy, enforced by the backend when each Delivery is planned:
  - optional event type: the master switch **and** the per-event preference must both be ON. A missing value is OFF;
  - mandatory event type (`event.cancelled`, `event.rescheduled`): the personal opt-out does not apply;
  - in every case the Global Admin Policy and the administrator's rule are checked first (ADR-0045 §2.4). Global Telegram OFF and a disabled rule block delivery, mandatory types included.
- The Engine's `PreferencePolicy` port receives both stored personal levels (`stored_enabled`, `master_enabled`). The policy itself is the event's specification gate: `PersonalOptInPreferencePolicy` or `MandatoryPreferencePolicy` (`app/notifications/preferences.py`).
- `GET`/`PUT /api/v1/me/notification-preferences` lets the authenticated user read and change only their own personal values (`docs/05-api/notification-preferences-api.md`). Mandatory and blocked types cannot be written. Frontend hiding is not the control.

### 2.2 Personal delivery requires a verified linked identity

A new Engine port, `RecipientReachability`, answers whether a personal Delivery on a channel can reach the user. For Telegram this means an **active** linked identity created by the user-started verified linking flow (ADR-0047 §4). Without one no Delivery is planned (`recipient_unreachable`), mandatory or not; the adapter's permanent `destination_unlinked` remains a second guard. Bot membership in a group never creates a personal destination. No business event type has another active channel in v1, so any other channel is unreachable here (fail closed).

### 2.3 Rendering context is a snapshot of the business transaction

`notifications.render_context` (JSONB object, default `{}`) stores the template's variables. They are written by the Engine in the same transaction as the business change, as a flat object of name → already formatted string: dates in the event's time zone, the old and new date of a reschedule. Resource links are stored only as resource ids. The context holds no secret, credential, internal note or private data of another person. The Engine validates its shape; the catalog boundary (§2.8) validates its names against the template's declared variables before anything is written.

### 2.4 Group/topic routes are a second recipient kind of the same Engine

- `notifications.recipient_user_id` becomes nullable and `notifications.recipient_destination_id` (FK `telegram_destinations`, RESTRICT) is added. CHECK: exactly one is set. A group publication is addressed to its route, not to a person.
- `NotificationRequest.destinations` lists candidate routes. For each route the Engine checks, in order:
  1. the Global Admin Policy (Telegram);
  2. the route rule — an installation-wide rule of the event type with `recipient_scope = telegram_destination`;
  3. the template;
  4. the route's own configuration: the row exists, is `enabled`, and its `notification_scope.event_types` (a JSON array, CHECK) lists the event type.

  It then creates a Notification addressed to the route, one `telegram` Delivery with `destination_type = telegram_destination`, and one outbox job. Recipient access, preferences and reachability are not consulted.
- Which event types may be published to routes is a catalog decision (PO decision 7): `event.created`, `event.cancelled`, `event.rescheduled`, and `news.published` when the news audience is `club`. The catalog boundary refuses route publication for any other type; the audience condition is checked by the integrating domain service.
- A route publishes nothing until an administrator configures it (an enabled route listing the event type) and enables its rule. Route discovery (`/bind@bot`, an unconfirmed candidate, administrator confirmation in the UI), diagnostics and route test send are a separate slice. Confirmed routes are stored in `telegram_destinations`; unconfirmed candidates never are. Real `chat_id`/`message_thread_id` come only from Telegram updates, never from names.

### 2.5 Safe Telegram rendering

The Telegram adapter renders the Notification's template from `render_context` and the template's declared variables (`app/notifications/rendering.py`) into Telegram `parse_mode=HTML`.

**Template syntax.** `{{name}}` refers to a declared variable. `**text**` is bold and must be balanced across the template. Everything else is literal text.

**Escaping.**
- Literal template text is HTML-escaped, so the only markup in a message is the `<b>` produced from `**`.
- Every value is HTML-escaped. Its `{`/`}` are emitted as numeric entities, and values are never bold-processed. A value can therefore neither inject markup nor produce a raw `{{placeholder}}`.

**Variables.**
- A required variable that is missing or blank, an undeclared or malformed placeholder, and unbalanced bold are a **permanent** `template_render_failed`. The same template and context can never succeed, so the existing retry policy does not retry it.
- An optional variable falls back to the catalog's value (cancellation reason "не указана", generic change summary). An empty fallback omits it together with one adjacent space; the news excerpt is omitted this way.

**Limits.**
- The visible text must be 1–4096 characters after rendering, counted as Telegram counts it after entity parsing; otherwise the failure is permanent (`message_too_long` / `template_render_failed`).
- A final check guarantees no literal `{{`/`}}` is ever sent.

The administrator test send stays plain text and creates no Notification, Delivery or outbox job (ADR-0048 §2.10).

### 2.6 Atomic planning; no savepoint (PO decision 14)

A domain service plans its notification **inside its own business transaction, before it commits**, through the catalog boundary (§2.8). The business change, the Notifications, Deliveries and outbox jobs commit together, or all roll back. The worker only sees committed jobs.

Planning is **not** isolated in a savepoint: there is no mechanism that would reliably record and re-plan a notification whose planning failed. Any Engine, contract or database error therefore propagates, and the whole business transaction rolls back. Neither the business change nor its notification is silently lost; the caller receives the error.

Policy outcomes are not errors. With Global OFF, no rule or a disabled rule, an inactive template, preference OFF or no linked identity, the business change commits with no Notification. A missing template is logged with safe fields (catalog §2). A Delivery failure after commit never touches the business transaction (ADR-0045 §2.9). Retries follow the existing worker policy (ADR-0046); Global OFF pauses Deliveries already queued (ADR-0048 §2.8).

### 2.7 Links

Message links are built only by the renderer from `APP_PUBLIC_BASE_URL` and a fixed path of an existing frontend page:

| Link | Page |
|---|---|
| Event | `/events?event=<uuid>` |
| News item | `/news/<uuid>` |
| Achievement | `/achievements` |

**`APP_PUBLIC_BASE_URL`:**
- Validated: absolute http(s), no credentials, query or fragment.
- Read by the outbox worker at start. An invalid value stops the worker; an unset value omits every link.
- The resource id is taken from the context and must be a UUID; no free-form value is placed in a URL.
- It is deployment configuration, not a secret and not integration configuration of ADR-0048.

`membership.approved` has no link (PO decision 9). Pages keep their normal authorization; a link reveals nothing to a recipient who is not authorized.

### 2.8 The catalog in code and the single integration boundary

`app/notifications/catalog.py` holds exactly the ten catalog keys. For each key it records:
- whether the key is mandatory;
- its status: `pending` until its slice integrates the domain service, `implemented` after it, or `blocked` — `membership.approved` is blocked until a canonical approval workflow exists (PO decision 12);
- its personal `recipient_scope`;
- whether group routing is allowed;
- its Telegram template code and declared variables. No business key has an Email template.

`app/notifications/business.plan_catalog_notification` is the only call a domain service makes. It plans only a key whose status is `implemented`: a `pending` or `blocked` key raises `CatalogNotificationError` before any database access, so nothing is written and the caller's business transaction rolls back. It is a thin adapter over the existing Engine that:
- builds the request from the catalog entry: Telegram only, the entry's preference policy, the linked-identity reachability check and the persisted Global Admin Policy;
- validates the render context;
- adds the subscribed routes when the service publishes to routes;
- composes idempotency keys (§2.9).

The domain service supplies the canonical audience and its recipient-access check. Guardians or parents are never added implicitly.

### 2.9 Idempotency

Keys are `<event_type>:<fact_id>:user:<user_id>` and `<event_type>:<fact_id>:dest:<destination_id>`. Notifications are UNIQUE on the key, and Deliveries are UNIQUE per (Notification, channel, destination). Planning the same business fact again — a repeated call, a concurrent duplicate or a replay — therefore never creates a second Notification, Delivery or outbox job.

`fact_id` identifies the business fact, chosen per event in its slice:
- the Event id of a cancellation or of the first publication;
- the News id of the first publication;
- the Award id;
- the audit record of a registration or attendance change;
- a UUID generated once per detected reschedule or location change, because event updates have no audit record and the same dates can legitimately recur (A→B→A→B is three facts). A repeated request with unchanged values detects no change and so produces no fact.

### 2.10 Initial state

- No migration of this decision creates a rule, template or route.
- Each slice seeds its templates with `is_active = true` and its rules (personal and, where allowed, route) with `is_enabled = false` (PO decision 13 and the template decision of 2026-10-09).
- An administrator enables a rule in Settings → Notifications after the slice has been verified. ADR-0048's template management is unchanged.

## 3. Consequences

### Positive

- One Engine for personal and group delivery; no parallel mechanism.
- Personal and group delivery are independent, and both are enforced on the server.
- Historical values are preserved, and rendering is deterministic and safe.
- The business change and its notification can no longer diverge.

### Negative / constraints

- A failure in planning now fails the business operation (by decision); an Engine defect surfaces as a failed request instead of a missing notification.
- `render_context` duplicates a few display values per Notification.
- Preferences are evaluated at planning time. Changing a preference does not cancel a Delivery that is already queued, but Global OFF still pauses it.
- The downgrade of the migration refuses while a route-addressed Notification exists, instead of deleting history.

## 4. Out of scope

- Domain integrations of the individual event types (separate slices, Issue #336).
- Route discovery, confirmation UI, diagnostics and route test send (separate slice).
- Personal preference and Telegram-link UI (separate slice).
- Email business rules, guardian/parent notifications, quiet hours, digests.
