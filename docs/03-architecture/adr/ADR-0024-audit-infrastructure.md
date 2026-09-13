# ADR-0024: Canonical audit infrastructure

## Status

Accepted. Closes ODR-015. Closes the audit-record-shape portion of ODR-013; **retention/deletion periods for audit records remain open** and are not decided by this ADR (see "Non-decisions" below).

**Amended by ADR-0025 §1**: the action vocabulary in §4 below is extended with `person.created`, `person.updated`, `person.archived` (PO/architect decision resolving Issue #62 GAP-9 — the closed vocabulary originally accepted here had no `Person`-mutation action at all). The list in §4 reflects the amended, current vocabulary; it was not always exactly this list — see ADR-0025 for the amendment record.

## Context

`docs/SYSTEM-SPECIFICATION.md` §3/§17, `docs/02-requirements/non-functional-requirements.md` §8/§10 and `docs/02-requirements/business-rules.md` §23 all require significant business mutations to be auditable, but no canonical `AuditLog` persistence contract, write boundary or failure-mode decision existed. ADR-0008's ODR-015 explicitly gated Issue #59 on resolving this before implementation. The pre-existing sketches in `docs/03-architecture/data-model.md` §19 and `docs/03-architecture/database-schema.md` §19 (`target_type`/`target_id`, `ip_address`, `user_agent`, a bare `status` column) were not implemented anywhere in the codebase and are superseded by this ADR.

Two constraints shape the decision:

- ODR-013 (retention/deletion policy) is explicitly still open. This ADR must define the audit record shape and lifecycle *contract* (append-only, no automatic deletion) without inventing a retention period, TTL or deletion job.
- The project's existing security documentation (`docs/07-security/security-and-privacy.md` §6, §12, §13) already prohibits secrets/credentials in logs and audit records; this ADR must not weaken that.

## Decision

### 1. Persistence model — `audit_logs`

One table, one reusable contract for every audit-required business action:

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID (ADR-0010) | no | primary key, immutable |
| `occurred_at` | `timestamptz` | no | UTC instant the action was recorded; server-generated at insert time (same convention as every other `created_at` in this codebase) |
| `actor_type` | string, CHECK `IN ('user','system')` | no | who performed the action |
| `actor_user_id` | UUID FK -> `users.id` (`RESTRICT`) | yes | required when `actor_type='user'`, forbidden when `actor_type='system'`; no synthetic "System" `User` row is ever created |
| `club_id` | UUID FK -> `clubs.id` (`RESTRICT`) | yes | the Club **context** of the event; never used as an authorization mechanism |
| `action` | string, CHECK against the closed vocabulary in §4 | no | a stable business action code — never an HTTP method, URL or free UI text |
| `resource_type` | string | yes | paired with `resource_id`: both null, or both set (CHECK) |
| `resource_id` | UUID (opaque, ADR-0010) | yes | the affected domain object's id; not necessarily an FK (targets span many tables) |
| `outcome` | string, CHECK `IN ('success','failure')` | no | |
| `request_id` | string | yes | the existing per-request correlation value from `app.api.request_context` (`X-Request-ID` / server-generated fallback) — no second ID scheme is introduced |
| `correlation_id` | string | yes | an application-supplied identifier for linking multiple audit records to one broader business operation, when the caller already has one; no new distributed-tracing infrastructure is introduced to populate it |
| `details` | `jsonb` | yes | only explicit, hand-built safe data — never an automatically serialized ORM entity, ORM session, HTTP request or response object |

No `updated_at`, `created_by` or `updated_by`: `AuditLog` rows are never updated after insertion (see §3), so `occurred_at` is the only timestamp the contract needs.

Constraints:

- `ck_audit_logs_actor_type_valid` — `actor_type IN ('user','system')`;
- `ck_audit_logs_actor_user_id_consistent` — `(actor_type='user' AND actor_user_id IS NOT NULL) OR (actor_type='system' AND actor_user_id IS NULL)`;
- `ck_audit_logs_outcome_valid` — `outcome IN ('success','failure')`;
- `ck_audit_logs_action_valid` — `action` restricted to the closed vocabulary in §4 (same CHECK-constraint-per-closed-vocabulary pattern already used for `users.status`, `events.event_type`/`status`, `guardian_relationships.status`);
- `ck_audit_logs_resource_consistent` — `(resource_type IS NULL) = (resource_id IS NULL)` (same paired-nullable-columns pattern as `events.location_latitude`/`location_longitude`).

Indexes (database-schema.md §23's "audit logs by target/actor/timestamp" requirement, restated against the renamed columns): `occurred_at`; `actor_user_id`; `club_id`; `(resource_type, resource_id)`; `request_id`. No further indexing is added without an observed access pattern.

### 2. Actor/resource/Club attribution semantics

- `actor_type='system'` is used for actions with no authenticated human initiator (a scheduled job, an internal process). `actor_user_id` is `NULL` in that case — a fabricated "System" `User` row is explicitly rejected as a modeling choice; it would be indistinguishable from a real account and would need its own authentication/authorization treatment for no benefit.
- `actor_type='user'` requires `actor_user_id` to reference the authenticated `User` performing the action, resolved the same way every other authenticated identity in this codebase is resolved (`app.api.deps.get_current_principal` / `CurrentPrincipal.user_id`) — never a client-asserted value.
- `club_id` records the Club **context** the action occurred in (e.g., the Club a membership or event belongs to). It is descriptive only. Authorization for the action itself must already have been decided by the existing permission/scope layer (ADR-0013) before an audit record is written; `club_id` on `AuditLog` is never read back to make an authorization decision.
- `resource_type`/`resource_id` identify the affected domain object when the action has one (e.g., `resource_type="club_membership"`, `resource_id=<ClubMembership.id>`). Actions without a single concrete target (rare, given the vocabulary in §4) leave both `NULL`.

### 3. Immutability and the write boundary

`AuditLog` rows are append-only. The reusable service boundary (`app.audit.service.record_audit_event`) only ever inserts a new row; the codebase provides no update or delete operation for `AuditLog`, and application code must not add one. (This is an application-level guarantee, matching the existing "append-only by application policy" wording already in `database-schema.md` §19 for the superseded sketch — no additional database trigger is introduced solely for this ADR.)

Every domain module that needs to record an audit-required action must call `app.audit.service.record_audit_event(...)` rather than constructing/inserting an `AuditLog` row directly or inventing a parallel audit mechanism. This is the same "one shared service boundary, not ad-hoc per-caller logic" shape already used for Club-ownership validation (`app.authorization.club_ownership`, ADR-0022).

### 4. Audit-required action vocabulary (closed, as amended by ADR-0025 §1)

The following 28 action codes are accepted at this stage. The list is intentionally closed (enforced by `ck_audit_logs_action_valid`) and is **not** extended beyond what is listed without a corresponding ADR amendment and migration — see ADR-0025 §1 for the one amendment made so far (adding the three `person.*` actions to this ADR's original 25-action list).

```text
person.created
person.updated
person.archived

user.created
user.status_changed
user.locked
user.unlocked

membership.created
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

`action` values are stable business event codes, never an HTTP method, a URL path, or arbitrary UI text (mirrors `docs/05-api/api-conventions.md`'s general "stable machine-readable code" convention already used for the error contract).

Ordinary reads (`GET`), routine `404`s, routine validation errors, UI clicks, page views and other non-mutating or non-significant activity are explicitly **not** written as `AuditLog` rows under this ADR.

### 5. Transaction and fail-closed semantics

For an audit-required business mutation, the audit record is written in the **same database transaction** as the business mutation it documents:

```text
BEGIN
  business mutation
  audit insert (app.audit.service.record_audit_event)
COMMIT
```

If the audit insert fails for any reason (a canonical-vocabulary/actor-consistency validation error, a prohibited-secret `details` payload, or a database constraint violation), the caller must roll back the entire transaction — the business mutation must not be left committed without its accompanying audit record. This is fail-closed: an operation that requires an audit trail does not get to silently "succeed anyway" without one.

`record_audit_event` itself never calls `session.commit()` or `session.rollback()` — committing/rolling back the shared transaction remains the caller's responsibility, exactly like every other service function in this codebase that participates in a larger unit of work (e.g. `app.events.service.create_event_staff_assignment` owns and commits its own transaction because it *is* the whole unit of work; a future People/Membership service that must combine a business mutation with an audit record instead commits once, after both steps, and rolls back if either fails).

This ADR does not implement asynchronous audit delivery, and none is introduced by a message bus, background queue or otherwise: audit recording stays synchronous and part of the originating request's transaction, consistent with NFR-REL-001 ("критичные операции изменения данных должны выполняться транзакционно") and `docs/05-api/api-conventions.md` §22/§23 (API layer delegates transaction boundaries to the service layer; cross-entity operations that form one business action commit atomically).

### 6. Security / secret prohibition

`app.audit.service` rejects (does not mask, does not truncate, does not "best-effort sanitize") any `details` payload whose keys indicate a password, password hash, access/refresh/session/reset/verification/invitation token, API key, cookie, `Authorization` header value, or other credential/secret, at any nesting depth. This check runs both in the service function and as an ORM-level `@validates` hook on `AuditLog.details` itself, so a caller cannot bypass it by constructing the row directly (the same defense-in-depth shape already used for `Event.timezone` validation). `details` must otherwise be a plain JSON-safe value (`str`/`int`/`float`/`bool`/`None`/`dict`/`list` only) — an ORM entity, an HTTP request/response object, or a live SQLAlchemy `Session` can never be passed through and silently serialized; callers must build the safe payload explicitly (e.g. `{"changes": {"status": {"from": "active", "to": "suspended"}}}`).

`AuditLog` never gains an `ip_address` or `user_agent` column, and no HTTP method, path or full request body is ever written into `details`, per this Issue's explicit scope boundary — those remain, if needed, a concern of ordinary application/access logs, not `AuditLog`.

### 7. No audit API in this ADR

This ADR defines the persistence and service-layer write boundary only. It does not create `GET /audit` or any other audit read endpoint, does not create an `audit.read` permission or any new permission/scope/role, and does not implement an audit UI. A future audit-read API is a separate, later architectural decision.

## Non-decisions (still open)

- **Retention/deletion (ODR-013).** How long `AuditLog` rows are kept, whether/when they may be archived or deleted, and any legal-hold exception are **not** decided here. Until ODR-013 is closed for audit records specifically, `AuditLog` rows are kept indefinitely (no TTL, no retention job, no automatic cleanup) — consistent with `docs/03-architecture/data-retention-and-deletion.md` §4 and §6.
- **Wiring specific domain mutations to this infrastructure** (People/Membership, Event, Role/Group, Guardian write paths actually calling `record_audit_event`) is out of scope for this ADR and for Issue #59; those are separate implementation Issues that consume this infrastructure.
- **A public audit-read API / `audit.read` permission** is not designed or authorized here (§7).
- Extending the action vocabulary in §4 beyond what is listed requires a follow-up ADR update, not an application-level addition.

## Consequences

- Every future audit-required mutation has one canonical table, one canonical write function, and one canonical set of prohibited-secret rules to follow, instead of each domain module inventing its own.
- `AuditLog` rows can never reference a fabricated "System" `User`, and can never silently carry a password/token/secret, by construction (CHECK constraints plus the ORM-level validation hook).
- Because retention is intentionally undecided, `audit_logs` will grow unbounded until ODR-013 is closed; this is an accepted, explicit consequence rather than an oversight.
- Reconciles `docs/03-architecture/data-model.md` §19 and `docs/03-architecture/database-schema.md` §19, which previously described a different, unimplemented `AuditLog` shape (`target_type`/`target_id`, `ip_address`, `user_agent`, bare `status`); both documents are updated alongside this ADR to describe the model actually implemented here.

## Traceability

- Issue #59 — Foundation: define and implement canonical audit infrastructure
- ADR-0008 — Open decisions register (ODR-013, ODR-015)
- ADR-0010 — Primary key strategy
- ADR-0013 — Scope canonicalization
- ADR-0022 — Cross-Club ownership integrity (shared service-boundary precedent)
- `docs/03-architecture/data-retention-and-deletion.md`
