"""Request/response models for /api/v1/role-assignments (Issue #74).

Canonical source: docs/02-requirements/roles-and-permissions.md §19,
ADR-0026. Single-resource responses are returned directly per ADR-0014
(no `data` wrapper). `RoleAssignmentOut` matches the canonical
persistence model (app.db.authorization.UserRoleAssignment) field-for-
field.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class RoleAssignmentCreateRequest(BaseModel):
    """roles-and-permissions.md §19.1: `valid_from` is
    "серверно/доменом определяемое" (server/domain-determined) — never
    client-suppliable — so this schema has no `valid_from`/`valid_to`
    field at all, unlike Group/GroupMembership's own create requests.
    `scope_ref_id` is included (rather than omitted) so the router can
    detect and reject a non-NULL value with the specific error ADR-0026
    §2 requires, instead of Pydantic silently dropping an undocumented
    field.
    """

    user_id: UUID
    role_id: UUID
    scope_type: str
    club_id: Optional[UUID] = None
    scope_ref_id: Optional[UUID] = None


class RoleAssignmentOut(BaseModel):
    id: UUID
    user_id: UUID
    role_id: UUID
    club_id: Optional[UUID]
    scope_type: str
    scope_ref_id: Optional[UUID]
    valid_from: datetime
    valid_to: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class PersonRoleAssignmentCreateRequest(BaseModel):
    """`POST /persons/{person_id}/role-assignments` (TH-0112 / ADR-0039).

    Deliberately narrower than `RoleAssignmentCreateRequest`: only a
    canonical role code is accepted (ADR-0039 §3) — never a raw `role_id`,
    `user_id`, `scope_type`, or `club_id`. Identity and scope are always
    resolved/decided server-side (see app.role_assignments.person_roles's
    module docstring).
    """

    role_code: str


class PersonRoleAssignmentOut(BaseModel):
    """Response for the Person-scoped role-assignment endpoints. Exposes
    `role_code` (never `role_id`) so the client never needs to know a
    Role's opaque id to render or act on it."""

    id: UUID
    person_id: UUID
    role_code: str
    club_id: Optional[UUID]
    valid_from: datetime
