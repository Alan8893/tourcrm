# ADR-0026: RoleAssignment API and authorization semantics

## Status

Accepted; partially superseded by ADR-0027 for ClubMembership effectivity;
amended by TH-0089 / Issue #99 for the `all` + `club_id = NULL`
combination (see "Amendment (TH-0089 / Issue #99)" below).

## Context

Issue #73 (`Specification gate: RoleAssignment API`) identified five blocking gaps in the existing RoleAssignment contract: revoke semantics, scope combinations, cross-Club assignment integrity, role/permission catalog mutability, and authorization semantics for `role.manage`.

The persistence foundation already contains `UserRoleAssignment` with `user_id`, `role_id`, nullable `club_id`, `scope_type`, nullable `scope_ref_id`, timestamps, and a uniqueness constraint preventing duplicate assignments. The API inventory contains `GET /api/v1/role-assignments`, `POST /api/v1/role-assignments`, and `POST /api/v1/role-assignments/{id}/revoke`.

## Decision

### 1. RoleAssignment validity and revoke

`UserRoleAssignment` uses a temporal validity interval `[valid_from, valid_to)`.

- `valid_from` is the start of the assignment's validity.
- `valid_to = NULL` means an open-ended active interval.
- Revoke does not delete the assignment. It closes the interval by setting `valid_to` to the server-generated UTC time of the revoke operation.
- A revoked/ended assignment remains as historical data and is not reopened.
- Revoke of an already-ended assignment is rejected.
- A later re-assignment is a new `UserRoleAssignment` record.

Effective authorization considers only assignments valid at the time of the authorization check and all other authorization conditions.

### 2. Canonical RoleAssignment scope combinations

The following combinations are canonical in MVP:

| `scope_type` | `club_id` | `scope_ref_id` |
|---|---|---|
| `all` | required | `NULL` |
| `self` | required | `NULL` |
| `children` | required | `NULL` |
| `own_groups` | required | `NULL` |
| `own_events` | required | `NULL` |
| `none` | `NULL` | `NULL` |

`scope_ref_id` is not used by these MVP scopes.

`own_groups` is evaluated through the user's domain relationship to Groups, specifically the applicable GroupInstructorAssignment relationship. `own_events` is evaluated through EventStaffAssignment. RoleAssignment does not store a direct reference to one Group or Event for these scopes.

`self`, `children`, `own_groups` and `own_events` are always Club-scoped. Global assignments using these scopes are not supported in MVP.

`assigned_events` remains only an alias for `own_events`; `own_records` is not canonical.

### 3. Cross-Club integrity

A club-scoped RoleAssignment may be created only when the target User has an active `ClubMembership` in the target Club.

Ending the target User's ClubMembership does not automatically delete, revoke, close, or otherwise mutate the RoleAssignment. Whether membership is an additional prerequisite for later effective authorization is governed by ADR-0027 and domain-specific policies, not by this ADR.

The authorization context `club_id` on AuditLog remains descriptive only and is never itself an authorization grant, consistent with ADR-0024.

### 4. Role and permission catalog mutability

The current RoleAssignment slice manages assignments only.

The baseline/system roles `admin`, `instructor`, `member`, and `guardian` are existing reusable roles. Their permission sets are not mutable through the RoleAssignment API.

The current slice introduces no Role CRUD, Permission CRUD or RolePermission CRUD. `role.manage` cannot be used to modify the role or permission catalog.

New roles, permissions, scopes, or an explicit-deny model require separate accepted architectural/product decisions.

### 5. `role.manage` authorization

`role.manage` is the permission for managing RoleAssignment records. It is not permission to modify the Role/Permission catalog.

For MVP, a RoleAssignment that grants `role.manage` is valid only with scope `all`:

- `all` + `club_id = NULL`: the holder may manage RoleAssignments in any Club;
- `all` + a specific `club_id`: the holder may manage RoleAssignments only in that Club.

`self`, `children`, `own_groups`, `own_events` and `none` are invalid scopes for `role.manage`.

The caller's `role.manage` scope determines the target Club boundary. It does not bypass the independent validation of the target User's active ClubMembership, target Role, target assignment scope, lifecycle and uniqueness rules.

### 6. Audit

RoleAssignment mutations use the closed ADR-0024 action vocabulary:

- `role_assignment.created`
- `role_assignment.changed`
- `role_assignment.revoked`

Business mutation and audit insertion occur in the same transaction under ADR-0024 fail-closed semantics.

## Consequences

- Role assignments have durable history without destructive revoke.
- Authorization scope remains separate from object ownership and does not require `scope_ref_id` for `own_groups` or `own_events`.
- Active ClubMembership is required when creating a club-scoped RoleAssignment, preserving cross-Club integrity.
- Ending ClubMembership does not by itself revoke or invalidate an otherwise effective RoleAssignment; see ADR-0027.
- `role.manage` cannot be used to delegate arbitrary object-scoped role administration or mutate the RBAC catalog.
- The existing RoleAssignment persistence model requires temporal validity fields to implement this ADR if they are not already present.

## Amendment (TH-0089 / Issue #99): `all` + `club_id = NULL` is canonical

§2's original combination table marked `club_id` "required" for `all`,
with no exception. §5 of this same ADR, however, already described
`all + club_id = NULL` as a valid, meaningful combination — "the holder
may manage RoleAssignments in any Club" — without ever being able to
create one, since no API or operation in the codebase produced that
combination (§2's own validator rejected it) until TH-0089 (Issue #99,
"Initial administrator bootstrap") needed to create exactly this
combination for the global installation administrator (ADR-0027) and
surfaced the inconsistency.

This amendment resolves it in favor of §5's own, already-accepted
description: **`all` is the one scope_type whose `club_id` may be either
a specific Club (unchanged club-wide meaning) or `NULL` (installation-
wide — "all resources within the authorized ... system boundary", per
ADR-0013's own original wording for `all`)**. Every other scope's rule
is unchanged: `self`/`children`/`own_groups`/`own_events` still require
a specific `club_id`; `none` still requires `club_id = NULL`.

No new scope name, role, or permission is introduced. The read/
authorization side needed no change at all —
`app.authorization.service.scope_matches`/`club_boundary_matches` and
`app.role_assignments.authorization.role_assignment_visibility_filter`
already treated an `all + club_id = NULL` row (however it might be
created) as installation-wide; only the creation-time validator
(`app.role_assignments.lifecycle.validate_role_assignment_scope`) was
out of step with the rest of this same ADR. This is a correction of that
one validator, not a new architectural decision.

## Non-goals

This ADR does not introduce:

- Role CRUD;
- Permission CRUD;
- RolePermission CRUD;
- new roles;
- new permissions;
- new scopes;
- explicit deny semantics;
- invitation/registration role workflows;
- automatic role grants;
- a role-transfer abstraction.

## Traceability

- Issue #73 — Specification gate: RoleAssignment API
- Issue #74 — Implement RoleAssignment API
- ADR-0013 — Canonical Scope Vocabulary
- ADR-0017 — Identity and Authorization Documentation Canonicalization
- ADR-0022 — Cross-Club Ownership Integrity
- ADR-0024 — Canonical Audit Infrastructure
- ADR-0025 — People & Membership API decisions
- ADR-0027 — RoleAssignment and ClubMembership effectivity
