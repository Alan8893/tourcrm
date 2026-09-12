# TourCRM — Authentication and Authorization

## 1. Purpose

This document defines the target security model for account authentication and authorization.

## 2. Identity model

The system separates:

- `Person` — human being;
- `User` — authentication account;
- `ClubMembership` — relation to the club;
- `RoleAssignment` — granted role(s);
- `GuardianRelationship` — parent/legal representative relationship.

A user account may be associated with one Person. A Person may have several roles through role assignments where permitted.

## 3. Account states

Minimum User lifecycle:

```text
invited
  -> pending_registration
  -> pending_approval
  -> active
  -> suspended
  -> disabled
```

Password-reset and email-verification states are orthogonal authentication properties and must not be encoded by multiplying account statuses.

## 4. Registration modes

The system supports three onboarding modes:

### 4.1 Self-registration

1. User submits registration form.
2. System validates input and creates a pending account.
3. Email verification may be required according to configuration.
4. Administrator approves the account.
5. Account becomes active.

### 4.2 Invitation

1. Authorized administrator creates an invitation.
2. System issues a one-time, time-limited invitation token/link or code.
3. Invitee completes registration.
4. The invitation establishes the intended club/person relation but does not grant permissions beyond the configured role after validation.

### 4.3 Import

An administrator may import member/person records from an approved structured source.

Import creates or updates person/membership records according to an explicitly defined matching strategy. It must not silently create privileged accounts.

## 5. Login identifier

The project should initially support email as a login identifier, subject to uniqueness policy.

The authentication abstraction must not make email the only possible identity mechanism, allowing future username/SSO integration.

## 6. Password policy

The implementation must:

- never store plaintext passwords;
- use a modern adaptive password hash;
- enforce a configurable minimum password strength;
- prevent obvious compromised/common passwords where practical;
- support secure password reset;
- invalidate/revoke relevant sessions after sensitive credential changes.

Passwords must never appear in logs, audit records, analytics or API responses.

## 7. Session/token model

The final mechanism may be secure cookie-based sessions or short-lived access tokens with refresh/session management. The selection must be recorded in an ADR before implementation.

Required security properties:

- explicit expiration;
- revocation capability;
- protection against CSRF/XSS appropriate to the mechanism;
- secure transport in Internet deployment;
- no credential storage in URLs;
- secure handling of refresh/session material.

## 8. Email verification

Where email verification is enabled, the verification token must be:

- single-use;
- time-limited;
- stored hashed or otherwise protected at rest where practical;
- invalidated after use or expiration.

## 9. Password reset

Reset flow must not reveal whether an email/account exists in a way that enables trivial account enumeration.

Reset tokens must be:

- single-use;
- short-lived;
- cryptographically random;
- never logged or returned after issuance.

Successful password reset invalidates affected sessions according to the session policy.

## 10. Account approval

Administrator approval is a distinct workflow state from authentication.

A verified email does not by itself grant club membership or privileged access.

The approval operation must be auditable.

## 11. Invitation security

Invitation tokens/codes must:

- have an expiration;
- have explicit usage limits;
- be revocable;
- be tied to intended club context;
- not contain sensitive information;
- not grant more permissions than the invitation explicitly authorizes.

## 12. Roles

Initial application roles:

- `admin`;
- `instructor`;
- `member`;
- `guardian`.

A user may have multiple role assignments when business rules allow it.

## 13. Permissions

Permissions are atomic capabilities such as:

```text
member.read
member.create
member.update
member.archive
attendance.read
attendance.update
event.read
event.create
event.update
trip.create
trip.update
finance.read
finance.manage
user.manage
settings.manage
audit.read
```

The complete permission catalog is maintained in `docs/02-requirements/roles-and-permissions.md`.

## 14. Scope

Permissions may be constrained by scope:

- `all`;
- `own_groups`;
- `self`;
- `children`;
- `own_records`.

Scope is part of authorization and must be checked server-side.

## 15. Guardian access

A guardian may access child information only through an active GuardianRelationship and applicable permissions.

One guardian may have multiple children and one child may have multiple guardians.

A guardian relationship does not automatically grant access to every club record associated with the child; access is resource-specific.

## 16. Minor/member privacy boundary

The system must explicitly distinguish:

- data visible to the participant;
- data visible to a guardian;
- data visible to instructors;
- data visible only to administrators.

Medical, financial, audit and administrative data require explicit permissions and must not be exposed merely because the viewer is related to the participant.

## 17. Authorization algorithm

Conceptually:

```text
Authenticate User
      |
      v
Resolve Person + ClubMembership
      |
      v
Resolve active RoleAssignments
      |
      v
Resolve Permission
      |
      v
Evaluate Scope + Resource relationship
      |
      v
ALLOW / DENY
```

The default result is DENY.

## 18. Backend enforcement

All protected read and write paths must enforce authorization in the backend.

UI route guards and hidden buttons are not security controls.

List endpoints must apply authorization filtering before data is returned, preventing inference of unauthorized records through counts, search or pagination.

## 19. Privilege boundaries

Administrative operations must require explicit administrative permissions, not merely successful login.

A user must never be able to grant themselves roles, broaden their permissions, approve their own privileged registration or bypass authorization through direct API calls.

## 20. Role assignment changes

Role assignment creation, modification and revocation are security-sensitive and must be auditable.

Changes take effect according to the application session/cache strategy and must not leave stale elevated permissions active indefinitely.

## 21. Rate limiting and abuse protection

Authentication, password-reset, invitation and other abuse-prone endpoints should have rate limits and anti-automation controls.

A rate limit must not reveal whether a particular account exists.

## 22. Security logging

Security-relevant events should include:

- successful/failed login;
- password reset request/completion;
- email verification;
- account approval/rejection;
- invitation creation/revocation/use;
- role assignment changes;
- account suspension/disablement;
- suspicious authentication failures.

Logs must avoid sensitive credentials and must follow the audit/security retention policy.

## 23. Logout and revocation

Users must have a reliable logout operation.

Administrators need a documented mechanism to invalidate a compromised or suspended account's active sessions/tokens.

## 24. Future SSO

The identity model must leave room for future SSO integration without replacing Person/ClubMembership/RoleAssignment semantics.

External identity should map to an existing internal Person/User rather than becoming the canonical business identity.

## 25. Security acceptance criteria

Authentication/authorization implementation is accepted only when:

- unauthorized requests are denied;
- role/permission tests cover positive and negative cases;
- object-level scope tests exist;
- guardian/child access is tested;
- privileged operations require the intended permission;
- password and token secrets are absent from responses/logs;
- registration, invitation and reset workflows are covered;
- security-sensitive mutations are audited.
