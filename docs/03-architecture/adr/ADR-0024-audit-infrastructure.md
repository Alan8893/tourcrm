# ADR-0024: Canonical audit infrastructure

## Status

Accepted. Closes ODR-015. Closes the audit-record-shape portion of ODR-013; **retention/deletion periods for audit records remain open** and are not decided by this ADR.

**Amended by ADR-0025 §1:** adds `person.created`, `person.updated`, `person.archived`.

**Amended by ADR-0025 §11:** adds `membership.updated` for non-lifecycle changes to an existing `ClubMembership` (currently `membership_type`). The list in §4 is the current canonical vocabulary.

## Context

The system requires significant business mutations to be auditable, but a canonical persistence contract, write boundary and failure mode had not previously been defined. Existing unimplemented `AuditLog` sketches using `target_type`/`target_id`, `ip_address`, `user_agent` and a bare `status` are superseded by this ADR.

ODR-013 (retention/deletion) remains open. This ADR therefore defines the audit record shape and append-only lifecycle without inventing a retention period, TTL or cleanup job. Existing security requirements prohibiting secrets and credentials in logs remain in force.

## Decision

### 1. Persistence model — `audit_logs`

One table and one reusable contract are used for every audit-required business action:

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID | no | primary key, immutable |
| `occurred_at` | `timestamptz` | no | UTC, server-generated at insert time |
| `actor_type` | string, CHECK `user/system` | no | action initiator |
| `actor_user_id` | UUID FK -> `users.id` (`RESTRICT`) | yes | required for `user`, NULL for `system` |
| `club_id` | UUID FK -> `clubs.id` (`RESTRICT`) | yes | descriptive Club context, never authorization |
| `action` | string | no | stable code from the closed vocabulary in §4 |
| `resource_type` | string | yes | paired with `resource_id`; both null or both set |
| `resource_id` | UUID | yes | affected domain object; not necessarily an FK |
| `outcome` | string, CHECK `success/failure` | no | operation outcome |
| `request_id` | string | yes | existing request correlation value |
| `correlation_id` | string | yes | optional broader-operation correlation value |
| `details` | `jsonb` | yes | explicit safe data only; never automatic ORM/request serialization |

No `updated_at`, `created_by` or `updated_by`: audit rows are never updated.

Required constraints include actor consistency, valid outcome, valid closed action vocabulary, and paired `resource_type`/`resource_id` nullability. Indexes are provided for `occurred_at`, `actor_user_id`, `club_id`, `(resource_type, resource_id)` and `request_id`.

### 2. Actor, resource and Club semantics

- `actor_type=system` is used for actions without an authenticated human initiator; no synthetic System User exists.
- `actor_type=user` requires the authenticated user's real `actor_user_id`; clients cannot assert it.
- `club_id` is context only and is never consulted for authorization.
- `resource_type` and `resource_id` identify the affected object when applicable.

### 3. Immutability and write boundary

`AuditLog` is append-only. The shared service `app.audit.service.record_audit_event(...)` is the canonical write boundary. Domain modules must use it rather than inserting audit rows directly or creating parallel audit mechanisms.

The application provides no update/delete operation for audit rows. No database trigger is required solely to enforce this application-level policy.

### 4. Audit-required action vocabulary (closed)

The following **29 action codes** are canonical. The vocabulary must not be extended without a corresponding ADR amendment and migration.

```text
person.created
person.updated
person.archived

user.created
user.status_changed
user.locked
user.unlocked

membership.created
membership.updated
membership.status_changed
membership.ended

role_assignment.created
role_assignment.changed
role_assignment.revoked

group.created
group.updated
group_membership.created
group_membership.updated
group_membership.ended
group_instructor_assignment.created
group_instructor_assignment.updated
group_instructor_assignment.ended

guardian_relationship.created
guardian_relationship.updated
guardian_relationship.revoked

event.created
event.updated
event.status_changed
event.archived
```

`membership.updated` is for non-lifecycle changes to an existing `ClubMembership`, currently `membership_type`. It is distinct from `membership.status_changed` and `membership.ended`. `membership.ended` is emitted when `left_at` is first set. A status transition that also ends a membership period may emit both `membership.status_changed` and `membership.ended`.

`action` values are stable business codes, never HTTP methods, URL paths or arbitrary UI text.

Ordinary reads, routine `404`s, validation errors, UI clicks, page views and other non-significant activity are not audit records under this ADR.

### 5. Transaction and fail-closed semantics

For an audit-required mutation, the business mutation and audit insert occur in the **same database transaction**:

```text
BEGIN
  business mutation
  audit insert (app.audit.service.record_audit_event)
COMMIT
```

If the audit insert fails, the caller rolls back the whole transaction. An audit-required business mutation must not commit without its audit record.

`record_audit_event` does not commit or roll back; the caller owns the transaction boundary.

Audit recording is synchronous. No message bus, background queue or asynchronous delivery is introduced by this ADR.

### 6. Security / secret prohibition

The audit service rejects prohibited secrets at any nesting depth, including passwords, password hashes, access/refresh/session/reset/verification/invitation tokens, API keys, cookies, Authorization credentials and other secrets. It does not mask or silently sanitize them.

`details` must otherwise be plain JSON-safe data. Callers must explicitly construct safe payloads; ORM entities, HTTP requests/responses and SQLAlchemy sessions are never automatically serialized.

`AuditLog` has no `ip_address` or `user_agent` fields, and HTTP method/path or full request bodies are not written to `details`.

### 7. No audit API

This ADR defines persistence and the write boundary only. It does not create an audit-read API, `audit.read` permission or audit UI. Those require a separate decision.

## Non-decisions (still open)

- **Retention/deletion (ODR-013):** retention periods, archival/deletion rules and legal-hold exceptions are not decided here. Until resolved, audit rows are retained indefinitely with no TTL or cleanup job.
- Wiring every future domain mutation to the audit infrastructure is handled by separate implementation Issues.
- A public audit-read API and its authorization model are future work.
- Any further action-vocabulary extension requires an ADR amendment.

## Consequences

- All audit-required mutations share one canonical table, write function and secret-prohibition policy.
- Audit records cannot use a fabricated System User or silently contain credentials.
- `audit_logs` can grow without bound until ODR-013 is resolved; this is intentional.
- The canonical audit model supersedes the earlier unimplemented audit sketches in the data-model and database-schema documentation.

## Traceability

- Issue #59 — Foundation: define and implement canonical audit infrastructure
- Issue #62 — People & Membership API
- ADR-0008 — Open decisions register
- ADR-0010 — Primary key strategy
- ADR-0013 — Scope canonicalization
- ADR-0022 — Cross-Club ownership integrity
- ADR-0025 — People & Membership API decisions and audit-vocabulary amendments
- `docs/03-architecture/data-retention-and-deletion.md`
