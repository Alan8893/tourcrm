# ADR-0027 — Initial administrator bootstrap

## Status

Accepted.

## Context

TourCRM uses application-managed authentication. The authentication API already provides registration, login, session management and recovery, but the normal registration flow creates a `pending` User and therefore cannot be used to create the first usable administrator account.

The repository intentionally does not bootstrap an administrator inside the Identity foundation or Authentication implementation. This leaves a clean security boundary, but it also means a fresh installation currently has no defined path from an empty database to the first authenticated administrator.

The system must therefore define a deterministic first-install bootstrap path without introducing a permanent backdoor, default credentials, or an unauthenticated web endpoint.

## Decision

The first administrator is created through an **operator-controlled bootstrap operation outside the public HTTP authentication surface**.

The bootstrap operation is a deployment/administration concern and is not a normal `/api/v1/auth/*` endpoint.

### Bootstrap semantics

1. Bootstrap is allowed only when the installation has no existing active administrator account according to the canonical `admin` role and effective role-assignment model.
2. The bootstrap operation creates a normal `User` linked to a normal `Person`.
3. The new User is created directly as `active`; it does not pass through public self-registration approval because bootstrap is an installation-level administrative operation.
4. The bootstrap operation assigns the canonical `admin` role using the existing RoleAssignment model and its canonical scope rules.
5. The bootstrap operation must not create a new role, permission, scope, or alternative administrator identity.
6. It must use the existing password hashing/session/authentication infrastructure. Plaintext passwords are never stored, logged, returned, or embedded in source/configuration committed to the repository.
7. After a valid administrator exists, the bootstrap operation must refuse to create another initial administrator. It is not a general-purpose admin creation mechanism.
8. The bootstrap operation must be safe to run more than once: a second execution must fail without modifying the existing administrator or creating a second bootstrap account.
9. The bootstrap operation must be transactional: partial User/Person/RoleAssignment creation must not leave a usable half-created account.
10. The bootstrap path must not bypass normal authorization for subsequent administrator/user/role management. After bootstrap, ordinary administrative operations use the existing authentication + authorization model.

## Credential input

The preferred interactive mode is an operator prompt that does not echo the password.

An environment/configuration-driven mode may be supported for automated deployments, but the password must not be accepted as a command-line argument, written to repository files, or emitted into logs.

Exact CLI command name and implementation library are implementation details and are to be documented by the implementation PR.

## Administrator scope

The bootstrap administrator must receive the canonical administrator authority required for the club installation. The implementation must use the already-defined `admin` role and existing authorization semantics.

The bootstrap operation must not invent a new global role code, permission, or scope. If the existing role/permission catalog or grants required to make the canonical `admin` role effective are missing or inconsistent, bootstrap must fail clearly rather than silently inventing policy.

## Relationship to normal registration

Public registration remains unchanged:

`register -> pending -> approval/activation -> login`

Bootstrap is a separate installation path:

`empty installation -> initial admin bootstrap -> active admin -> login`

Email verification is not a substitute for administrator bootstrap and must not implicitly elevate a registered user to `admin`.

## Relationship to login UI

The web application has two distinct states:

### Unauthenticated with configured installation

Show the normal TourCRM login page.

The login form submits the user's identifier and password to the existing `POST /api/v1/auth/login` contract. On successful authentication, the server-side session cookie becomes the authenticated browser session and the application loads the current principal from `GET /api/v1/auth/me`.

### Unconfigured installation

The web application must not expose a public "create administrator" form by default. An operator completes the bootstrap operation first; afterwards the ordinary login page is used.

If the frontend needs to distinguish an unconfigured installation for deployment diagnostics, that state must be represented by a narrowly scoped, non-sensitive health/setup signal rather than by exposing whether particular users or credentials exist.

## Security properties

- No default username/password exists.
- No hardcoded administrator credentials exist.
- No public unauthenticated endpoint creates an administrator.
- Bootstrap cannot be used to create arbitrary additional administrators.
- Password hashing follows ADR-0009 and the existing authentication implementation.
- Bootstrap secrets never enter audit logs or operational logs.
- Existing session and authorization mechanisms remain authoritative after bootstrap.
- A failed bootstrap must not partially create a usable account.
- Concurrent bootstrap attempts must result in at most one successful initial administrator.

## Failure behavior

Bootstrap must fail if:

- an active administrator already exists;
- required canonical `admin` role data is missing;
- required canonical role/permission state is inconsistent;
- supplied credentials fail the existing password policy;
- the database transaction cannot be completed safely.

Failure must not disclose passwords, hashes, tokens, or internal security data.

## Consequences

### Positive

- A clean installation has a deterministic path to the first login.
- There is no permanent bootstrap backdoor in the public API.
- The first administrator becomes a normal TourCRM User and uses the same session/authentication system as every other user.
- The architecture remains compatible with LAN and Internet deployment.

### Negative

- Initial installation requires an operator/deployment step before the first web login.
- Automated deployment needs a secure secret-injection mechanism if non-interactive bootstrap is used.

## Non-goals

- Admin UI for creating additional administrators.
- Invitation implementation.
- Self-registration policy changes.
- Role/Permission CRUD.
- New authentication providers, SSO, MFA or passkeys.
- Changes to the canonical role/permission matrix.

## Traceability

- `docs/03-architecture/adr/ADR-0009-authentication-mechanism.md`
- `docs/05-api/auth-api.md`
- `docs/05-api/auth-and-authorization.md`
- `docs/02-requirements/roles-and-permissions.md`
- Issue #19 — Identity: Role, Permission и RoleAssignment foundation
- Issue #29 — Authorization: RBAC + permission scope enforcement foundation
- Issue #33 — Authentication: application-managed sessions, registration, login and recovery
- Issue #74 — RoleAssignment API
