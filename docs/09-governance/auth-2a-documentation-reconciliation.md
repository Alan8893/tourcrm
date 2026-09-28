# TourCRM — AUTH-2A Documentation Reconciliation

## 1. Status

- **Status:** Accepted
- **Date:** 2026-09-28
- **Decision:** Product Owner + CTO
- **Canonical architecture:** ADR-0041 — Permission-level authorization scopes

This document records the documentation boundary introduced by AUTH-2A. It does not introduce new authorization policy; it prevents older persistence/API wording from being interpreted as the current authorization model.

## 2. Canonical authorization model

Authorization follows:

```text
User
  → RoleAssignment
      → Role
          → Permission
              → PermissionScope(s)
                  → Resource/Object
```

Scope belongs to the **permission grant**, not to the role assignment.

`RolePermissionScope` stores one or more canonical scopes for a specific `RolePermission`. A scope attached to one permission never affects another permission.

The authorization engine MUST NOT use `UserRoleAssignment.scope_type` as authorization input.

Canonical scopes are:

- `all`
- `self`
- `children`
- `own_groups`
- `own_events`
- `none`

`assigned_events` is an alias of `own_events`. `own_records` is not a canonical scope.

## 3. Role assignments

A user has at most one active assignment of a given role within the same Club.

Person Detail and the Person creation wizard create one `UserRoleAssignment` per role. Contextual access is expressed by the scopes of that role's individual permission grants; the same role is not duplicated to obtain multiple scopes.

`UserRoleAssignment.scope_type` remains only as a legacy persistence field during the transition and has no authorization meaning.

The generic role-assignment create API does not accept an authorization scope. Legacy clients may send `scope_type`, but it is ignored. New assignments may retain `all` in the legacy column; that value does not grant `all` authorization.

A Club is required for every role assignment except a global assignment of the system `admin` role.

## 4. Current role/scope policy

The product-level source of truth remains `docs/02-requirements/role-permission-scope-matrix.md`.

The current canonical role-level scope requirements relevant to AUTH-2A are:

| Role | Permission scope pattern |
|---|---|
| `admin` | `all` |
| `instructor` | permissions may use `own_groups` and `self` independently |
| `member` | permissions use `self` where granted |
| `guardian` | permissions may use `children` and `self` independently |

This table describes the scope model, not permission seeding. AUTH-2A does not add permissions to instructor, member or guardian.

## 5. Legacy migration

AUTH-2A migration behavior is fail-closed:

- active `admin` assignments with legacy `scope_type != all` are closed instead of being upgraded to full administrative access;
- duplicate active assignments of the same role within one user and Club are closed, retaining the earliest assignment;
- closed assignments remain as history and receive the normal system audit record;
- a `RolePermission` without a `RolePermissionScope` does not authorize access.

## 6. Documentation precedence

Where older documentation describes `scope_type` as an authorization property of `UserRoleAssignment`, that wording is superseded by ADR-0041 and this reconciliation record.

In particular, the following concepts are no longer canonical:

- `scope_type` selected by the caller when assigning a role;
- authorization derived from the scope of the role assignment;
- multiple active assignments of the same role solely to express multiple permission scopes;
- `role.manage` authorization derived from the assignment's `scope_type`.

Resource-specific scope predicates remain defined by their respective domain/API contracts. AUTH-2A changes where the scope is attached in the authorization model; it does not invent new object-level predicates.

## 7. Explicit non-goals

This reconciliation does not:

- seed the full role-permission matrix;
- add permissions to instructor, member or guardian;
- change the single-Club product model;
- change frontend role behavior;
- introduce explicit deny semantics;
- redefine unresolved Event/Group business policy.

For the normative architectural decision, see ADR-0041.
