"""RoleAssignment API domain service (Issue #74 / ADR-0026).

See app.role_assignments.service for the canonical create/revoke
mutations, app.role_assignments.lifecycle for scope-combination and
revoke-transition validation, app.role_assignments.authorization for
`role.manage` visibility scoping, and app.role_assignments.queries for
the `GET /role-assignments` list query.

This package does not implement Role CRUD, Permission CRUD,
RolePermission CRUD, new roles/permissions/scopes, or explicit-deny
semantics — all explicit non-goals of ADR-0026.
"""
