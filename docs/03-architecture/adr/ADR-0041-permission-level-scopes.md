# ADR-0041 — Permission-level authorization scopes

- **Status:** Accepted
- **Date:** 2026-09-28
- **Decision owners:** Product Owner + CTO
- **Related:** ADR-0013, ADR-0026, ADR-0035, AUTH-1, AUTH-2A

## 1. Context

TourCRM uses RBAC with permission scopes. The initial authorization model stored `scope_type` on `UserRoleAssignment`, which made one scope apply to every permission granted by a role.

That model cannot represent the canonical role-permission matrix when different permissions of the same role require different scopes. For example, an instructor may need `person.read(self)` while an operational permission is scoped to `own_groups`; a guardian may need child access for some permissions while retaining self-access for personal data.

The implementation of AUTH-2A introduces permission-level scopes through `RolePermissionScope`.

## 2. Decision

### 2.1 Scope belongs to the permission grant

The canonical authorization chain is:

```text
User
  → RoleAssignment
      → Role
          → Permission
              → PermissionScope(s)
                  → Resource/Object
```

`RolePermission` identifies a role-to-permission grant. `RolePermissionScope` defines one or more canonical scopes for that specific grant.

The effective scope for a permission is the union of the scopes configured for that permission across the user's active role assignments. Scopes configured for another permission do not affect it.

The authorization engine MUST NOT use `UserRoleAssignment.scope_type` to authorize a permission.

### 2.2 Canonical scopes

Only the scopes defined by ADR-0013 are valid:

- `all`
- `self`
- `children`
- `own_groups`
- `own_events`
- `none`

`assigned_events` is an alias of `own_events`; `own_records` is not a canonical scope.

### 2.3 Multiple scopes for one permission

A single `RolePermission` may have multiple `RolePermissionScope` rows. Any matching scope is sufficient for that permission, subject to the resource/object policy.

A scope attached to permission A MUST NOT grant, restrict, or otherwise influence permission B.

### 2.4 Role assignments

A user has at most one active assignment of a given role within the same Club. The uniqueness rule is independent of authorization scope.

Person Detail and the Person creation wizard create one `UserRoleAssignment` per role. Role-specific contextual authorization is expressed by the scopes of that role's individual permission grants, not by creating multiple assignments of the same role.

`UserRoleAssignment.scope_type` is retained as a legacy persistence field during the transition. It is not an authorization input.

### 2.5 Role assignment API

The generic role-assignment API does not allow the caller to choose an authorization scope. `scope_type` is not part of the canonical create request.

For compatibility with legacy persistence, newly created assignments may retain the legacy column value `all`; this value has no effect on authorization.

A Club is required for every role assignment except a global assignment of the system `admin` role.

### 2.6 Legacy data migration

The AUTH-2A migration applies scopes only to existing `RolePermission` grants; it does not introduce new role permissions for instructor, member or guardian.

The migration is fail-closed with respect to legacy authorization:

- active `admin` assignments with a legacy scope other than `all` are closed rather than upgraded to full administrative access;
- when several active assignments of the same role exist for one user and Club, the earliest assignment is retained and later duplicates are closed;
- closed assignments remain in the database and receive the normal system audit record; historical data is not deleted;
- an existing permission grant without a corresponding `RolePermissionScope` does not authorize access until a scope is explicitly configured.

## 3. Consequences

### Positive

- The authorization model can represent the canonical role-permission matrix without duplicating role assignments.
- Different permissions of one role can have different scopes.
- Adding a scope to one permission cannot accidentally broaden another permission.
- Role assignment lifecycle remains a single role-level lifecycle: one active assignment, one revoke operation.

### Trade-offs

- The authorization data model has an additional `RolePermissionScope` relation.
- Existing code and tests that constructed scope through `UserRoleAssignment` must use permission-level scope grants.
- `UserRoleAssignment.scope_type` remains temporarily for persistence compatibility and must not be treated as canonical authorization state.

## 4. Compatibility and migration boundary

AUTH-2A does not seed the full role-permission matrix. The existing role grants remain the source of which permissions are assigned; this decision only defines how scopes are attached to those grants.

The canonical role-permission matrix in `docs/02-requirements/role-permission-scope-matrix.md` remains the product-level source of truth for which role receives which permission and scope. Any remaining discrepancies between that matrix and older ADR/API prose must be reconciled in documentation; they must not be resolved by implementation inference.

## 5. Non-goals

This ADR does not:

- add permissions to instructor, member or guardian;
- define new business permissions;
- change resource-specific scope predicates;
- change the single-Club product model;
- change the frontend role model;
- introduce explicit deny semantics.
