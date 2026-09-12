# TourCRM — Authentication Persistence Contract

## 1. Назначение

Документ определяет canonical persistence contract для application-managed authentication из ADR-0009. Он дополняет логическую модель и database schema требованиями, которые необходимы для server-side sessions и одноразовых authentication challenges.

Документ является нормативным для будущей реализации authentication persistence. Он не является реализацией и не выбирает конкретные ORM classes, token libraries или framework middleware.

Canonical references:

- `docs/03-architecture/adr/ADR-0009-authentication-mechanism.md`
- `docs/03-architecture/database-schema.md`
- `docs/03-architecture/data-model.md`
- `docs/05-api/auth-api.md`
- `docs/05-api/auth-and-authorization.md`
- `docs/02-requirements/roles-and-permissions.md`
- `docs/03-architecture/adr/ADR-0010-primary-key-strategy.md`

## 2. Общие security rules

1. Authentication is application-managed through the TourCRM backend.
2. Authenticated browser sessions are server-side revocable records.
3. Raw session credentials and one-time challenge secrets MUST NOT be stored in plaintext when a hash-based lookup is sufficient.
4. Raw credentials and challenge secrets MUST NOT appear in API responses, ordinary application logs, audit records, exception details or metrics.
5. One-time challenges MUST be single-use. Successful consumption permanently prevents reuse.
6. Expiry MUST be checked server-side using the authoritative server time.
7. Revocation MUST be server-side and effective immediately for subsequent authentication checks.
8. IDs of persistence entities follow ADR-0010: stable opaque UUID identifiers are used for independent entities.
9. Historical/security records MUST NOT be physically deleted merely to revoke a credential. Retention/deletion remains subject to ODR-013.
10. Security state changes are auditable, but audit records contain metadata about the event, never raw credentials.

## 3. Authenticated sessions

### `authenticated_sessions`

Represents one server-side authenticated application session.

Fields:

- `id` — UUID PK;
- `user_id` — required FK to `users`;
- `session_token_hash` — required, unique secure hash of the presented session secret;
- `status` — required lifecycle state: `active`, `revoked`, `expired`;
- `created_at` — required;
- `last_seen_at` — required/updated when the session is successfully used according to session policy;
- `expires_at` — required;
- `revoked_at` — nullable;
- `revoked_reason` — nullable;
- `created_ip_address` — nullable;
- `created_user_agent` — nullable;
- `last_seen_ip_address` — nullable.

Constraints and rules:

- `session_token_hash` is unique;
- `expires_at` must be later than `created_at`;
- `revoked_at` is required when `status = revoked`;
- an expired session cannot become active again;
- revocation is idempotent;
- the raw session secret is never persisted;
- session listing returns safe metadata only, never the raw secret or its hash;
- `logout` revokes the current session;
- `logout-all` revokes all active sessions belonging to the authenticated user;
- deleting a User must not silently leave an active session usable.

Recommended indexes:

- unique index on `session_token_hash`;
- `(user_id, status)`;
- `(expires_at, status)` for cleanup/expiry processing.

## 4. Email verification challenges

### `email_verification_challenges`

Represents a single email-verification attempt/challenge.

Fields:

- `id` — UUID PK;
- `user_id` — required FK to `users`;
- `token_hash` — required, unique secure hash of the verification secret;
- `expires_at` — required;
- `consumed_at` — nullable;
- `revoked_at` — nullable;
- `created_at` — required.

Rules:

- token is single-use;
- token cannot be consumed after expiry;
- token cannot be consumed after `consumed_at` or `revoked_at` is set;
- successful verification sets `consumed_at` and performs the corresponding User verification state change;
- raw verification token is never persisted or logged;
- repeated use has no credential-disclosing side effect;
- a new challenge may supersede older outstanding challenges according to the authentication service policy, but an old challenge must never become valid again after consumption/revocation.

Recommended indexes:

- unique index on `token_hash`;
- `(user_id, expires_at)`;
- `(user_id, consumed_at, revoked_at)` where useful for active-challenge lookup.

## 5. Password reset challenges

### `password_reset_challenges`

Represents a single password-reset attempt/challenge.

Fields:

- `id` — UUID PK;
- `user_id` — required FK to `users`;
- `token_hash` — required, unique secure hash of the reset secret;
- `expires_at` — required;
- `consumed_at` — nullable;
- `revoked_at` — nullable;
- `created_at` — required.

Rules:

- token is single-use;
- token cannot be consumed after expiry;
- token cannot be consumed after `consumed_at` or `revoked_at` is set;
- successful password reset marks the challenge consumed and invalidates previously active authenticated sessions according to `auth-api.md`;
- raw reset token is never persisted or logged;
- reset-request responses must not disclose whether an account exists.

Recommended indexes:

- unique index on `token_hash`;
- `(user_id, expires_at)`;
- `(user_id, consumed_at, revoked_at)` where useful for active-challenge lookup.

## 6. Invitations

The existing `invitations` persistence entity in `database-schema.md` remains the canonical invitation record.

Required security semantics:

- `token_hash` stores only a secure hash/reference, never the raw invitation token;
- `expires_at` is mandatory for validity;
- `used_at` makes a successful invitation single-use;
- `revoked_at` makes an invitation immediately invalid;
- an invitation is valid only while not expired, not used and not revoked;
- raw invitation tokens are never returned by ordinary API responses after the creation/delivery flow and are never logged;
- invitation acceptance must not grant permissions beyond the invitation policy;
- invitation creation authorization is currently unresolved as described in ODR-014 below.

Existing fields:

- `id` — UUID PK;
- `club_id` — required FK;
- `token_hash` — required;
- `email` — nullable;
- `role` — nullable;
- `expires_at` — required;
- `used_at` — nullable;
- `revoked_at` — nullable;
- `created_by` — required FK;
- timestamps.

No new invitation role-grant semantics are introduced by this document.

## 7. Lifecycle and referential integrity

- Authentication entities reference `users` with restrictive historical semantics rather than destructive cascade where that could invalidate security history.
- Revocation/consumption is represented by state and timestamps, not physical deletion.
- Expired challenges may later be physically removed only under the approved retention/deletion policy; this does not change their security semantics while retained.
- Security records must remain auditable even when the corresponding User is disabled, suspended or archived.

## 8. API traceability

The persistence model supports the behaviors already required by `docs/05-api/auth-api.md`:

- authenticated session creation and server-side revocation;
- current-session lookup;
- session listing;
- single-session revoke;
- logout-all;
- email verification;
- password reset request and confirmation;
- invitation creation and acceptance;
- expiry and single-use enforcement.

Authentication persistence does not itself grant authorization. Protected operations still follow:

`Authenticated User -> RoleAssignment -> Permission -> Scope -> Resource`

as defined by the authorization specification.

## 9. Open permission inconsistency

`docs/05-api/auth-api.md` currently names `membership.invitation.create` for invitation creation, but that permission code is not present in the canonical permission catalog in `docs/02-requirements/roles-and-permissions.md`.

This is intentionally **not resolved by this document**. Implementers MUST NOT silently substitute another permission code or invent a new permission. The discrepancy is tracked as **ODR-014** in `ADR-0008-open-decisions.md` and must be resolved at the specification level before invitation authorization is implemented.

## 10. Non-goals

This document does not define:

- password hashing algorithm or library version;
- session cookie implementation details;
- authentication middleware;
- login/register endpoint implementation;
- role grants;
- invitation role policy;
- audit implementation;
- retention periods;
- MFA, SSO or external identity providers.
