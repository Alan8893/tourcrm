# ADR-0009 — Authentication mechanism

## Status

Accepted for baseline architecture; exact library/version is implementation detail.

## Context

TourCRM is a multi-user web application for a school tourism club. It must support LAN and Internet access, desktop/tablet/mobile browsers, self-registration, administrator approval, invitations, password recovery, session revocation and future integrations.

## Decision

Use application-managed authentication through the TourCRM backend API.

The baseline model is:

- user identity is represented by `User` linked to `Person`;
- credentials are stored and verified by the backend;
- passwords are stored only as strong password hashes using a current password-hashing algorithm selected at implementation time;
- authentication results in an authenticated application session;
- session revocation is supported server-side;
- refresh/session credentials are never exposed to application logs or audit records;
- email verification is supported where email is used as an authentication identifier;
- password reset uses time-limited, single-use tokens;
- invitation links/codes are time-limited and single-use by default;
- authentication is independent of club membership and role assignment;
- authorization is evaluated after authentication using the permission/scope model defined in the security specification.

The initial release does not require an external identity provider, enterprise SSO, OAuth social login or MFA. The architecture must not prevent those capabilities from being added later.

## Rationale

The club requires controlled onboarding and administrator approval. Application-managed authentication provides predictable behavior in LAN deployments and does not create an external identity-provider dependency for the first release.

## Security requirements

- Never store plaintext passwords.
- Never return password hashes from API responses.
- Apply brute-force protection and login throttling.
- Use secure session/token transport over HTTPS in Internet deployments.
- Support explicit logout/revocation.
- Record security-relevant authentication events in audit/security logs without storing secrets.
- Use generic responses where necessary to reduce account enumeration risk.

## Consequences

Positive:

- works in isolated LAN deployments;
- supports the required registration and invitation flows;
- keeps identity lifecycle under club control.

Negative:

- TourCRM is responsible for credential security;
- future external SSO requires an additional adapter/integration layer.

## Implementation notes

Claude must select currently maintained libraries and document exact choices separately. This ADR intentionally defines behavior and architecture, not mutable dependency versions.