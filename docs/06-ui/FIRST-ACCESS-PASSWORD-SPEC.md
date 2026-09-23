# TourCRM — First-Access Password UX Specification

**Status:** APPROVED PRODUCT UX / CONTRACT GAP REQUIRES ADR BEFORE IMPLEMENTATION
**Decision date:** 2026-09-23
**Scope:** administrator-issued one-time credential and first login
**Owner:** Product Owner

## 1. Product decision

When an administrator creates or resets credentials for a managed User, the system issues a temporary one-time credential.

The user enters this one-time credential in the **ordinary Login form, in the normal password field**.

After successful authentication with the one-time credential, the user must be sent to a **mandatory password-change screen** and establish a permanent password before continuing to the normal authenticated application.

The temporary credential is:
- single-use;
- temporary / expiry-bound;
- invalidated after successful first-access password setup or expiry.

## 2. User flow

```text
Administrator issues first-access/reset credential
        ↓
User opens normal Login
        ↓
identifier + one-time credential in normal password field
        ↓
successful authentication
        ↓
mandatory Change Password screen
        ↓
permanent password established
        ↓
normal authenticated application
```

The user must not be required to understand that the temporary credential is a special token or use a separate token-entry UI.

## 3. Administrator UI — temporary credential

When the administrator is shown the one-time credential:
- provide a reliable «Скопировать» action;
- successful copy must produce an explicit success state;
- failed copy must produce an explicit failure state and must not claim success;
- the UI must not silently alter or truncate the credential;
- the credential is shown only where the existing account-management authorization permits it.

This specification records the observed real-server bug where the copy action failed.

## 4. Security

Permanent passwords are never displayed to administrators.

The temporary credential must not be persisted or logged as a permanent password.

Backend remains authoritative for validity, expiry, single-use semantics, authentication state and authorization.

## 5. Current contract gap

The current authentication API documentation describes administrator-issued first-access credentials through the existing password-reset confirmation mechanism and states that a newly created administrator-managed User has no password hash before setup.

The approved UX above intentionally requires the one-time credential to be accepted by the normal Login flow and then transition into a mandatory password-change state.

These are not yet reconciled contracts.

**Therefore implementation must not invent an ad-hoc parallel authentication mechanism.** Before implementation, the authentication domain/API contract and the relevant ADR must be updated to define:
- how the temporary credential is represented in authentication state;
- how normal login recognizes it;
- how the mandatory password-change state is represented and enforced;
- how the one-time credential is consumed;
- session issuance/restrictions before permanent password setup;
- expiry and replay behavior;
- API error semantics.

## 6. Traceability

Product decision source: GitHub Issue #179 — PO Decision — Role-aware UX audit and acceptance findings (2026-09-23).

Current authentication baseline: docs/05-api/auth-api.md §15 and docs/05-api/people-api.md §24.2.

This document does not authorize implementation until the contract GAP above is reconciled.