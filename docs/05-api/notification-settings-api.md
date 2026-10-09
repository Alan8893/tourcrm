# Notification Settings API

- **Status:** Implemented (Issue #333)
- **Architecture:** ADR-0048 (Administrator Notification Settings and UI-managed secrets), ADR-0045 (Notification Center), ADR-0047 §6 (Telegram configuration)
- **Code:** `apps/api/app/api/v1/notification_settings.py`, `apps/api/app/notification_settings/`
- **UI:** Settings → Notifications (`/settings/notifications`, `apps/web/src/pages/NotificationSettingsPage.tsx`)

Installation-wide administration of notification delivery: the Global Admin Policy, existing installation-wide rules, SMTP and Telegram bot configuration with write-only secrets, channel readiness and a test send. TourCRM is single-club: there is no club-level policy or configuration in this API.

## Authorization

Enforced by the backend for every request (UI hiding is not a security mechanism):

| Permission | Endpoints |
|---|---|
| `notification.manage` | `/policy`, `/rules`, `/status`, `/telegram-destinations`, `/test-send` |
| `settings.manage` | `/integrations…` (non-secret settings and secrets) |

Both are existing admin-only permissions; they are checked against the installation's single Club (with no Club, or more than one, every request is denied). Errors: `401 unauthorized`, `403 forbidden` (also for a missing/invalid CSRF header on state-changing requests). All paths below are under `/api/v1/settings/notifications`.

## Global Admin Policy

### `GET /policy`

```json
{ "email_enabled": false, "telegram_enabled": false, "saved": false }
```

`saved: false` means no policy has been saved: every channel is OFF (fail closed).

### `PUT /policy`

Body `{"email_enabled": true, "telegram_enabled": false}` — both required, strict booleans, no other fields (no `club_id`, no MAX). Returns the saved policy. Audited as `notification_policy.updated` with per-channel before/after values.

A channel that is OFF blocks delivery: the Notification Engine creates no Delivery for it, and the worker ends an already queued Delivery as a terminal `channel_disabled_by_policy` failure. The Telegram switch does not affect Telegram account linking or the poller.

## Rules

### `GET /rules`

Collection of the existing installation-wide rules (`club_id = NULL`): `id`, `event_type`, `channel`, `recipient_scope`, `is_enabled`. An empty list is a normal state: rules exist only for business events that passed their ADR-0045 §5 specification gate.

### `PATCH /rules/{rule_id}`

Body `{"is_enabled": false}` — the only editable field. `404 rule_not_found` for an unknown or club-specific rule. Audited as `notification_rule.updated` (only when the value changes). There is no create or delete endpoint.

## Status

### `GET /status`

```json
{
  "encryption": "available",
  "email": { "policy_enabled": true, "configuration": "configured", "ready": true },
  "telegram": { "policy_enabled": false, "configuration": "not_configured", "ready": false, "linking_available": true }
}
```

- `encryption`: `available` | `missing` | `invalid` — state of the `SETTINGS_ENCRYPTION_KEYS` key ring (never the keys).
- `configuration`: `not_configured` | `incomplete` (Email: host or sender missing, or SMTP username without password and vice versa) | `invalid` | `secret_unavailable` (a stored secret cannot be decrypted: no key, wrong/unknown key, corrupted ciphertext) | `configured`.
- `ready` = `policy_enabled` and `configuration == "configured"`.
- `linking_available`: a bot username is configured, so `POST /api/v1/me/telegram-link/challenges` can issue deep links.

## Integrations (`settings.manage`)

### `GET /integrations`

`Cache-Control: no-store`.

```json
{
  "encryption": "available",
  "email": {
    "smtp_host": "smtp.example.org", "smtp_port": 587, "smtp_security": "starttls",
    "smtp_username": "mailer", "sender_email": "noreply@example.org", "sender_name": "Клуб",
    "password_configured": true
  },
  "telegram": { "bot_username": "club_bot", "bot_token_configured": true }
}
```

Secrets appear **only** as `*_configured` booleans: no endpoint ever returns a secret value, its ciphertext or an encryption key.

### `PUT /integrations/email`

Replaces every non-secret SMTP field: `smtp_host` (host name, IPv4 or `[IPv6]`), `smtp_port` (1–65535; `null` = default of the mode: 587/465/25), `smtp_security` (`starttls` | `ssl` | `none`), `smtp_username`, `sender_email`, `sender_name`. `null`/empty clears a field. Never reads or changes the SMTP password; a body with any other field (for example a `password`) is rejected with `422`. Errors: `422 invalid_smtp_host`, `invalid_sender_email`, `invalid_sender_name`, `invalid_smtp_username`, `validation_error`. Audited as `notification_integration.updated` with the changed field **names** only.

### `PUT /integrations/telegram`

Body `{"bot_username": "club_bot"}` (`@` prefix accepted, `null` clears). `422 invalid_bot_username`. The username must belong to the token's bot; the poller verifies it with `getMe`.

### Secrets: `PUT` / `DELETE /integrations/email/password`, `PUT` / `DELETE /integrations/telegram/bot-token`

- `PUT` body `{"value": "<new value>"}` (1–1024 characters) sets or replaces the secret and returns `{"configured": true}`. The bot token must have the format issued by @BotFather; an SMTP password must not contain line breaks. Errors: `422 invalid_secret` / `validation_error` (the value is never echoed in an error), `503 settings_encryption_unavailable` when the key ring is missing or invalid — nothing is stored, there is no plaintext fallback.
- `DELETE` clears the secret (`204`, idempotent; does not need the key). The UI asks for confirmation; no password re-entry is required.
- Audited as `notification_secret.set` (`details.setting`, `details.replaced`) and `notification_secret.cleared` (`details.existed`) — never the value or the ciphertext.

Storage: AES-256-GCM token `v1.<key_id>.<nonce>.<ciphertext+tag>` in `integration_secrets`, the secret identifier bound as associated data; see ADR-0048 §2.6 and `docs/08-infrastructure/infrastructure-and-devops.md` §9.1 (key ring, rotation).

## Test send (`notification.manage`)

### `GET /telegram-destinations`

The existing **enabled** `telegram_destinations` a test may be sent to: `id`, `name`, `topic_name`.

### `POST /test-send`

| Body | Destination |
|---|---|
| `{"channel": "email", "destination_kind": "email_address", "email": "a@b.org"}` | any valid address entered by the administrator |
| `{"channel": "telegram", "destination_kind": "own_telegram_account"}` | the administrator's own linked Telegram account |
| `{"channel": "telegram", "destination_kind": "telegram_destination", "telegram_destination_id": "<uuid>"}` | an existing enabled destination (with its topic) |

No other fields are accepted — a client-supplied chat id is rejected. `422 invalid_email` / `invalid_test_destination` for malformed requests (not counted).

Response `200`:

```json
{ "test": true, "channel": "email", "destination_kind": "email_address", "status": "delivered", "error_code": null }
```

`status: "failed"` carries a safe `error_code`: configuration (`channel_not_configured`, `channel_incomplete`, `channel_invalid`, `channel_secret_unavailable`), destination (`telegram_account_not_linked`, `destination_not_found`, `destination_disabled`) or the adapters' provider codes (`smtp_*`, `telegram_*`). Provider texts, credentials and raw responses are never returned or stored.

- Sent synchronously through the existing Email/Telegram adapters; the message is a fixed text marked as a test. No Notification, Delivery or outbox job is created; no database transaction is open during the provider call. The Global Admin Policy is not consulted (a test usually precedes switching a channel on).
- Rate limit: at most **5 attempts per administrator in any rolling 10 minutes**, successful and failed alike, stored in PostgreSQL (`notification_test_send_attempts`) — it holds across processes and restarts. Exceeding it: `429 too_many_requests` (audited, not counted).
- Every attempt is audited as `notification_test_send.attempted` with outcome, channel, destination kind and error code — never the address, chat id or message.
