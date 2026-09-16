# ADR-0027 — Initial administrator bootstrap

## Status

Accepted, amended by TH-0091.

## Context

TourCRM uses application-managed authentication. The normal registration flow creates a `pending` User and therefore cannot be used to create the first usable administrator account.

The application also has a Club-bound domain model, while the current MVP has no user-facing Club creation mechanism. A fresh installation therefore needs one deterministic initialization path that creates both the first usable Club context and the first administrator.

The domain remains multi-Club capable. This decision only defines the initial state of a fresh installation.

## Decision

The first installation is initialized through an **operator-controlled bootstrap operation outside the public HTTP API**.

The bootstrap creates, in one transaction:

1. one active primary `Club`;
2. one normal `Person`;
3. one active `User` linked to that Person;
4. one `UserRoleAssignment` using the canonical `admin` role and Club-bound `all` scope;
5. the existing canonical audit events for the supported resources.

No public unauthenticated bootstrap endpoint is introduced.

### Primary Club

For a fresh installation the bootstrap creates exactly one primary Club using the existing `Club` entity and its canonical fields.

- `name` is supplied interactively by the operator;
- `status` is `active`;
- no additional Club metadata is invented;
- no second Club is created by repeat bootstrap;
- future authorized Club creation remains a separate feature and the domain remains multi-Club capable.

The bootstrap administrator receives:

```text
role        = admin
scope_type  = all
club_id     = <primary Club ID>
scope_ref_id = NULL
```

This gives the initial administrator an explicit Club context required by current Club-bound application flows.

### Administrator identity

The bootstrap creates a normal `Person` + `User` rather than a separate administrator entity.

The initial Person uses the existing bootstrap placeholder identity `Admin Admin`; it may later be changed through ordinary account-management functionality.

The existing `User.email`/login identifier is used. No additional username/login field is introduced.

### Credential input

The preferred mode is an interactive operator prompt with password input hidden.

The password must not be:

- accepted as a command-line argument;
- read from repository files;
- stored in environment/configuration for this interactive mode;
- written to logs or CLI output;
- returned through an API;
- embedded in source code or Docker images.

### Repeat and concurrency

Bootstrap is allowed only for a fresh installation with no existing Club and no existing effective bootstrap administrator.

The existing PostgreSQL transaction advisory lock remains authoritative. Concurrent first-run attempts are serialized so at most one creates the primary Club and administrator.

A repeat bootstrap fails before mutation and never creates another Club or administrator.

If a Club already exists, bootstrap refuses with a clear safe error. Existing Club data is never deleted or modified by bootstrap.

### Transactionality

Club + Person + User + UserRoleAssignment + their supported audit events are one transaction. Any failure rolls the entire operation back.

No usable half-created bootstrap account or orphaned primary Club may remain.

### Role and permissions

The canonical `admin` Role is reused; it is not created dynamically.

Bootstrap does not seed or modify `RolePermission` grants and does not introduce a new permission, role, scope, or authorization mechanism. Existing RolePermission state remains authoritative.

### Normal login

After successful bootstrap, authentication uses the normal application flow:

```text
POST /api/v1/auth/login
        -> authenticated session
GET  /api/v1/auth/me
```

There is no special bootstrap login path.

## Security properties

- No default credentials.
- No hardcoded administrator credentials.
- No public unauthenticated administrator-creation endpoint.
- Bootstrap cannot be used to create arbitrary additional administrators.
- Password hashing remains delegated to the existing authentication infrastructure.
- Bootstrap secrets never enter audit or operational logs.
- Existing authentication and authorization remain authoritative after bootstrap.
- Concurrent bootstrap attempts cannot create multiple primary Clubs or initial administrators.
- Failed bootstrap rolls back completely.

## Failure behavior

Bootstrap fails if:

- an effective initial administrator already exists;
- a Club already exists;
- the canonical `admin` role is missing or inconsistent;
- supplied credentials fail the existing password policy;
- the supplied administrator identifier is already registered;
- the database transaction cannot be completed safely.

Failures do not disclose passwords, hashes, tokens, or other security-sensitive data.

## Consequences

### Positive

- A clean installation immediately receives the Club context required by the current MVP domain.
- The first administrator is a normal TourCRM User.
- Normal login/session/authorization mechanisms are reused.
- The multi-Club domain model is preserved for future expansion.

### Negative

- Initial installation requires an operator bootstrap step before first login.
- The first Club currently has no public management flow; that remains a later feature.

## Non-goals

- Public Club CRUD API.
- Club management UI.
- Single-tenant refactor.
- Additional administrator creation through bootstrap.
- Role/Permission CRUD or RolePermission policy changes.
- Login UX changes.
- MFA/SSO/OAuth/WebAuthn.

## Traceability

- `docs/03-architecture/adr/ADR-0009-authentication-mechanism.md`
- `docs/03-architecture/adr/ADR-0013-scope-vocabulary.md`
- `docs/03-architecture/adr/ADR-0026-role-assignment-api-decisions.md`
- `docs/05-api/auth-api.md`
- `docs/05-api/auth-and-authorization.md`
- `docs/02-requirements/roles-and-permissions.md`
- TH-0089 / Issue #99
- TH-0091 / Issue #104
