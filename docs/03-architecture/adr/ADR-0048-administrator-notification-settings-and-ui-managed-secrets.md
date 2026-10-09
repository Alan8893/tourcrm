# ADR-0048 — Administrator Notification Settings and UI-Managed Secrets

- **Status:** Accepted
- **Date:** 2026-10-09
- **Decision owner:** Product Owner / CTO
- **Decision type:** Architecture decision
- **Refines:** ADR-0045 (Notification Center and Communication Architecture), Feature Settings Governance
- **Related:** #317, ADR-0046, ADR-0047, `docs/09-governance/feature-settings.md`, `docs/04-modules/notifications-and-communications.md`
- **Amended:** 2026-10-09 — Product Owner implementation decisions for #333 (§2.6–§2.11); ADR-0047 §6 aligned

## 1. Context

TourCRM currently has notification persistence, an Engine, an asynchronous PostgreSQL outbox worker, Email and Telegram adapters, and Telegram user linking. The Engine depends on an Admin Policy port and fails closed when no policy source is connected. Administrator APIs/UI for notification policy, notification rules, integration configuration and test-send are still required.

TourCRM currently operates as a single-club installation. Administrators may not have shell/server access or specialist infrastructure knowledge, so routine configuration of SMTP and the Telegram bot must be available through the product UI.

## 2. Decisions

### 2.1 One global notification policy

The MVP has one installation-wide Global Admin Policy. Do not implement a separate Club Admin Policy API or UI while TourCRM operates as a single-club installation.

The effective policy chain is:

```text
Global Admin Policy
    ↓
Notification Rule
    ↓
User Preference
    ↓
Delivery
```

Each narrower level may restrict but never expand the level above it. Global OFF is absolute. Admin OFF always overrides User ON. If no applicable rule exists, no Delivery is created; there is no implicit allow-by-default.

Existing nullable `club_id` columns are retained for compatibility and future evolution. In this scope, administrator-managed notification rules are installation-wide (`club_id = NULL`). Club-specific policy and rule management require a future explicit product/architecture decision.

### 2.2 Settings UI scope

An authorized administrator can use Settings → Notifications to:

- enable/disable Email and Telegram at the installation level;
- manage installation-wide notification rules and their event/channel/audience/enabled settings, within the canonical event specifications;
- configure SMTP connection details and sender identity;
- configure Telegram bot username and bot token;
- see channel readiness/status without exposing credentials;
- send a safe test message through a configured channel;
- replace or clear a stored secret explicitly.

The UI must not invent notification event semantics. Each event still requires its own specification gate under ADR-0045 §5. MAX remains an extension point only and is not configurable as an implemented channel in this slice.

### 2.3 Secret storage and masked UI

Secrets managed through the UI include at minimum the SMTP password and Telegram bot token. Secret values are write-only after submission.

- The API never returns a stored secret, including in detail responses, errors, test-send responses, or audit data.
- After save, the UI shows a fixed mask (for example `••••••••`) and a configured/not-configured state. The mask is a display placeholder, not the secret value.
- The client must not submit the placeholder when saving unrelated settings.
- Replacing a secret requires an explicit replacement action and a newly entered value. Clearing requires a separate explicit action and authorization.
- Secret values are encrypted before persistence. The encryption key is supplied by deployment secret configuration, is not stored in the application database beside ciphertext, and must not be exposed through Settings UI.
- If the encryption key is missing or invalid, secret reads/writes and dependent channel operations fail closed with a safe, actionable status; plaintext fallback is prohibited.
- Secrets and encryption keys must not appear in logs, audit records, telemetry, exception messages, API payloads, outbox payloads, or delivery error text.
- SMTP non-secret settings (host, port, username, sender address and TLS mode where supported) and Telegram bot username may be returned to authorized administrators.
- Credential changes are audited by actor, setting identifier, timestamp and outcome only; audit entries never contain secret values.

The implementation must use authenticated encryption with integrity protection and a documented key-rotation/re-encryption strategy. Authorization must be enforced by the backend, not only by hiding UI controls.

### 2.4 Test-send behavior

Only an authorized administrator may initiate a test send. The UI requires an explicit destination (for example an email address or the linked administrator's Telegram account), validates the destination and reports a safe result. Test messages must be clearly marked as tests and must not create or simulate a business-event notification. Provider errors are normalized to safe codes/messages and must not include credentials or private payload content.

### 2.5 Auditing and authorization

- Backend authorization is authoritative for reading/updating notification settings, changing secrets, clearing secrets, editing rules and sending tests.
- Configuration and secret changes are audited according to the existing audit policy.
- Audit logs record the action and result, never old/new secret values.
- Settings never grant permissions to business resources.
- Secret read-back is prohibited, including for administrators.

### 2.6 Configuration source and secret encryption (PO decision, #333)

- SMTP and Telegram runtime configuration is stored in PostgreSQL and managed only through Settings → Notifications. Environment variables are **not** an alternative or fallback source for SMTP host/port/security/username/sender, the SMTP password, the Telegram bot username or the bot token. The integration secret store is separate from feature settings and from notification/outbox data.
- The only deployment secret of this feature is the encryption key ring `SETTINGS_ENCRYPTION_KEYS`: a comma-separated list of `<key_id>:<base64 32-byte key>` entries. The **first** key encrypts every new value; the others are accepted only for decrypting existing values. Key ids are short public labels; keys never leave deployment secret configuration and are never stored in the database.
- Encryption is AES-256-GCM through the `cryptography` library (no custom cryptography). Each stored value is a self-describing token carrying a format version, the key id, a random 96-bit nonce and the ciphertext with its authentication tag. The secret's identifier is bound as associated data, so a ciphertext cannot be moved to another secret slot.
- A missing, malformed or wrong key, an unknown key id, a tampered ciphertext or an unsupported format fails closed: the secret is reported as unavailable, writes are refused, dependent channel operations fail with a safe status. There is no plaintext storage and no environment fallback.
- Key rotation: add the new key first in `SETTINGS_ENCRYPTION_KEYS` and keep the old one after it; redeploy every process; run the re-encryption CLI (`python -m app.cli.reencrypt_settings_secrets`), which re-encrypts every stored secret with the first key in one transaction and reports counts only; after it reports nothing left under old keys, remove the old key. The CLI never prints secrets or plaintext and aborts without writing anything if any value cannot be decrypted.

### 2.7 Authorization (PO decision, #333)

- `notification.manage` — the Global Admin Policy, installation-wide notification rules, channel readiness and test send.
- `settings.manage` — integration configuration (SMTP, Telegram bot) and setting, replacing and clearing secrets.
- Both are existing admin-only permissions of the canonical catalog; no role receives new grants. Both are checked by the backend against the installation's single Club, like other installation-wide administration.
- Clearing a secret requires an explicit separate action confirmed in the UI; re-entering the administrator's password is not required.

### 2.8 Global policy and rules (PO decision, #333)

- The Global Admin Policy is per channel (Email, Telegram). When no policy has been saved, every channel is OFF (fail closed).
- Global OFF blocks delivery: the Engine creates no Delivery for a disabled channel, and the worker does not send an already queued Delivery while its channel is disabled — it ends as a terminal `channel_disabled_by_policy` failure.
- The Telegram switch does not affect Telegram account linking or the poller (ADR-0047 §6).
- Administrators can list existing installation-wide rules (`club_id = NULL`) and only enable/disable them. Rules are not created or deleted through the UI; no `event_type`, `recipient_scope` or business event is invented. An empty rule list is valid. No business event that has not passed its ADR-0045 §5 specification gate is wired to the Engine by this decision.

### 2.9 Runtime application (PO decision, #333)

Saved settings take effect without restarting the API, worker or poller: the worker reads the current SMTP/Telegram configuration for every delivery attempt, the API reads the current bot username for every linking deep link, and the poller re-reads its configuration between polls (ADR-0047 §6). Configuration is always read in a short session that is closed before any network I/O.

### 2.10 Test send (PO decision, #333)

- Email: to any syntactically valid address entered by the administrator, through the existing Email adapter and SMTP transport.
- Telegram: to the administrator's own linked Telegram account, or to an existing enabled `telegram_destination` (group/topic); an arbitrary client-supplied chat id is never accepted.
- Synchronous; no business Notification, Delivery or outbox job is created; no database transaction is open during the provider call. The message and the response are explicitly marked as a test; provider failures are reported as the adapters' stable safe codes.
- Rate limit: at most 5 test-send attempts per administrator in any rolling 10 minutes, counting successful and failed attempts, enforced through PostgreSQL so it holds across processes and restarts.

### 2.11 Audit (PO decision, #333)

Audited with actor, action, outcome and safe metadata only: Global Admin Policy changes, rule enable/disable, non-secret integration setting changes (changed field names, not values), secret set/replace/clear (which secret and whether a value already existed — never the value or ciphertext), and every test-send attempt including failures (channel, destination kind, safe error code — never the address, chat id, message, secret or provider response). The canonical audit vocabulary (ADR-0024 §4) is extended accordingly.

## 3. Consequences

### Positive

- Non-technical administrators can configure channels without shell access.
- Secrets remain masked and are never retrievable through the API.
- A single global policy avoids unnecessary club-level complexity for the current product.

### Negative / constraints

- The application needs a secure encryption-key configuration and key lifecycle.
- Secret rotation and recovery must be documented and tested.
- The Settings API/UI must distinguish unchanged, replace, and clear secret operations.
- Test-send operations need rate limiting and safe error handling.

## 4. Out of scope

- Multi-club policy administration.
- MAX channel implementation.
- General-purpose secret manager or arbitrary environment-variable editor.
- Visual notification-template editor.
- Defining business semantics for individual notification event types.
- Creating broad announcements or marketing campaigns.
