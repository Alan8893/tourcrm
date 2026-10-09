# ADR-0047 — Telegram Long Polling and User Linking

- **Status:** Accepted
- **Date:** 2026-10-09
- **Decision owner:** Product Owner / CTO
- **Decision type:** Architecture decision
- **Refines:** ADR-0045 (Notification Center and Communication Architecture), ADR-0046 (PostgreSQL-backed Worker)
- **Related:** #317, #329, \`docs/03-architecture/database-schema.md\`, \`docs/04-security/authentication-and-authorization.md\`, \`docs/04-ux/notifications.md\`

## 1. Context

TourCRM's Notification Center has a Telegram channel vocabulary, Telegram group/topic destination persistence, a channel adapter contract and an asynchronous PostgreSQL outbox worker. The Telegram Bot API adapter and secure linking of a TourCRM User to a Telegram identity are not implemented.

A publicly reachable HTTPS endpoint for the TourCRM deployment has not been confirmed. The implementation must not assume that Telegram can send inbound HTTPS requests to the deployment.

Telegram requires the user to initiate a private conversation with the bot before the bot can send them a private message. User linking therefore requires an authenticated, user-driven one-time flow initiated in TourCRM and completed by a trusted Bot API update.

## 2. Decision

For the MVP, use **Telegram Bot API long polling (\`getUpdates\`) in a dedicated process** for incoming updates. Do not implement a webhook receiver in this slice.

Long polling is selected because it does not require an inbound public HTTPS endpoint. Outbound HTTPS access from the worker host to the Telegram Bot API is still required.

The Telegram integration has two independent runtime responsibilities:

1. **Outbound delivery adapter** — implements the existing \`ChannelAdapter\` contract and is invoked by the Notification Delivery worker.
2. **Inbound update poller** — a separate CLI/service that receives Telegram updates and invokes the linking application service.

The poller is not an in-process FastAPI background task and is not part of the PostgreSQL outbox delivery worker. It does not create or dispatch notification deliveries.

## 3. Long-polling reliability contract

### 3.1 Single active poller

Only one active \`getUpdates\` poller may use a given bot token at a time. Deployment documentation must make this a single-replica service. The process must handle Telegram's conflict response safely and emit a secret-free operational error rather than starting competing poll loops.

The bot token is deployment/integration secret configuration. It must never be stored in ordinary feature settings, PostgreSQL business rows, update checkpoints, outbox payloads, API responses, logs, exception messages or object representations.

### 3.2 Durable update checkpoint

Telegram update acknowledgement is controlled by the \`offset\` passed to a subsequent \`getUpdates\` request. TourCRM must process updates sequentially and advance the persisted checkpoint only after the update's durable processing has committed.

- Store the last successfully processed \`update_id\` (or an equivalent next-offset checkpoint) in PostgreSQL.
- Processing the update and advancing the checkpoint must occur in the same transaction where applicable.
- The next poll uses the persisted checkpoint plus one as the offset.
- Do not advance an offset past updates that have not been durably processed.
- If the process crashes before commit, the update must be eligible for replay.
- If the process crashes after commit but before the next poll, replay/confirmation must not repeat the business effect.
- Duplicate or old update IDs are harmless and idempotently ignored.

Do not persist raw Telegram update bodies beyond the processing transaction unless a future ADR explicitly justifies durable raw-event storage. Never log complete update bodies or message text.

### 3.3 Runtime behavior

The poller must use bounded network timeouts, handle SIGTERM/SIGINT, retry transient transport/API failures with bounded backoff, and provide structured safe logs. It must not leak bot tokens, one-time linking tokens or private message content.

## 4. User identity linking

### 4.1 Start flow

1. An authenticated TourCRM user requests a linking challenge from the backend.
2. The backend generates a cryptographically random, URL-safe one-time token and returns the deep link to the configured bot. The raw token is shown only for this linking action.
3. PostgreSQL stores only a secure hash of the token, its owner, creation/expiry and lifecycle state.
4. The user opens the bot deep link and presses Start. The bot receives \`/start <token>\` in a private chat.
5. The poller passes the Telegram-supplied sender identity and token to the linking application service.
6. The service validates and consumes the challenge atomically and binds the Telegram identity to the challenge's TourCRM User. The update checkpoint and successful link must commit atomically.
7. The bot sends a safe confirmation. It must not echo the token.

The link payload must conform to Telegram's deep-link start parameter constraints. A raw linking token must never be placed in a general notification or ordinary application log.

### 4.2 Persistence and invariants

The implementation must introduce explicit persistence for the linked Telegram identity and one-time linking challenges, with an Alembic migration. The physical model must enforce, using database constraints where possible:

- one Telegram numeric user ID is linked to at most one TourCRM User;
- one TourCRM User has at most one active Telegram identity;
- a challenge token hash is unique;
- a challenge can be consumed at most once;
- a challenge expires according to authoritative server time;
- unlink/revoke is a lifecycle transition, not destructive deletion of security history.

Re-linking, replacement and unlinking behavior must be explicit and tested. A Telegram identity already linked to another TourCRM User must never be silently transferred. The browser cannot supply a Telegram ID as proof of ownership; the identity must come from the Bot API update.

### 4.3 Security and privacy

- Generate tokens with a cryptographically secure random generator.
- Persist a one-way secure token hash, never the raw token.
- Apply bounded expiry and rate-limit challenge creation/reissue.
- Unknown, expired, consumed and revoked tokens must produce the same safe external outcome.
- Challenge consumption and identity uniqueness checks must be atomic under concurrency.
- Audit only metadata such as action, actor and outcome; never tokens or message bodies.
- Linking is accepted only from a private chat with a valid sender identity; group messages cannot link a user.
- The bot's response must not reveal whether a different user or challenge exists.

## 5. Telegram Channel Adapter

The outbound adapter implements the existing \`ChannelAdapter\` boundary and uses the Telegram Bot API \`sendMessage\`.

Supported existing destination types:

- \`user\`: resolve the recipient's active linked Telegram identity;
- \`telegram_destination\`: resolve the existing \`TelegramDestination\` row and use \`chat_id\` plus optional \`message_thread_id\`.

The existing \`telegram_destinations\` model remains the canonical group/topic routing store. Topic names are display metadata; \`message_thread_id\` is the routing identity.

For this slice, send the stored Telegram-channel template \`body_template\` as plain text. Do not add HTML/Markdown rendering, a template editor or new notification policy behavior.

The adapter must not hold a database transaction during network I/O. It returns a safe \`ChannelResult\`; the existing outbox worker owns retries, backoff and terminal Delivery transitions. Network errors, rate limits and provider responses are classified into stable safe codes. Raw provider response bodies must not be persisted or logged. A network timeout after Telegram may have accepted a message remains at-least-once and may result in a duplicate; exactly-once provider delivery is not promised.

## 6. Configuration and operations

- \`TELEGRAM_BOT_TOKEN\` is supplied through environment-specific secret management.
- The bot username/configuration needed to construct a deep link must be validated at startup or obtained from a safe Bot API identity check; it is not a secret.
- Missing Telegram configuration must not break FastAPI startup when Telegram is unused. A Telegram delivery with no configured adapter follows the existing worker's bounded \`channel_adapter_unavailable\` behavior.
- The poller is a separate deployable process and must be explicitly enabled in deployment configuration.
- No Redis, Celery, new broker, webhook ingress or public endpoint is required for this MVP.
- Group/topic management UI and notification settings UI remain separate implementation slices.

## 7. Webhook alternative and future migration

Webhook was considered but is not selected now. It requires Telegram to reach a publicly accessible HTTPS endpoint and requires webhook secret-token validation, endpoint hardening and deployment/network configuration. That prerequisite is currently unconfirmed.

If TourCRM later has a confirmed public HTTPS endpoint and webhook is operationally preferred, introduce a separate ADR and replace only the update transport. The linking application service, challenge persistence, identity invariants, Notification Engine, Delivery model and outbound adapter contract must remain unchanged. The system must never run webhook and long polling concurrently for the same bot token.

## 8. Consequences

### Positive

- The MVP works without inbound public HTTPS.
- User linking is based on Telegram-authenticated updates, not a client-supplied Telegram ID.
- Durable checkpointing makes polling recoverable and replay-safe.
- Inbound linking and outbound delivery are independently deployable and testable.
- A future webhook migration does not require redesigning linking business rules.

### Trade-offs

- A dedicated long-running poller service must be deployed and monitored.
- Only one poller may run per bot token.
- Long polling requires outbound network access and durable update checkpoint handling.
- At-least-once delivery can produce duplicate messages after ambiguous provider outcomes.
