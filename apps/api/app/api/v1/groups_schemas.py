"""Request/response models for /api/v1/groups, /api/v1/group-memberships
and /api/v1/group-instructor-assignments (Issue #71).

Canonical source: docs/05-api/people-api.md §14-16. Single-resource
responses are returned directly per ADR-0014 (no `data` wrapper).
`*Out` models match the canonical persistence field lists (ADR-0021 §1-3)
field-for-field.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class GroupCreateRequest(BaseModel):
    """people-api.md §14: a created Group always gets `status = 'active'`
    — this schema has no `status` field at all, so a client cannot
    smuggle a different starting status through creation regardless of
    payload content."""

    club_id: UUID
    name: str
    description: Optional[str] = None
    valid_from: datetime
    valid_to: Optional[datetime] = None


class GroupUpdateRequest(BaseModel):
    """PATCH: only `name`/`description`/`valid_from`/`valid_to` — no
    `status`/`club_id` field exists on this schema at all (people-api.md
    §14: `status` changes only through `POST .../archive`; `club_id` is
    immutable after creation)."""

    name: Optional[str] = None
    description: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


class GroupOut(BaseModel):
    id: UUID
    club_id: UUID
    name: str
    description: Optional[str]
    status: str
    valid_from: datetime
    valid_to: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class GroupMembershipCreateRequest(BaseModel):
    """people-api.md §15: the API accepts `person_id`; the persistence
    model stores `club_membership_id` — the backend resolves Person to
    the target Group's Club membership and performs cross-Club
    validation (ADR-0022 §4). No `membership_status` field: a created
    membership always starts `active`."""

    person_id: UUID
    valid_from: datetime
    valid_to: Optional[datetime] = None


class GroupMembershipUpdateRequest(BaseModel):
    """PATCH: only `valid_from` is a genuinely mutable field
    (people-api.md §15). `group_id`/`club_membership_id`/
    `membership_status` are intentionally included here (rather than
    omitted, unlike GroupUpdateRequest) so the router can detect and
    reject an attempt to set them with the specific
    `group_membership_immutable_field` error code the canonical contract
    requires, instead of Pydantic silently dropping or genericly
    rejecting them.
    """

    valid_from: Optional[datetime] = None
    group_id: Optional[UUID] = None
    club_membership_id: Optional[UUID] = None
    membership_status: Optional[str] = None


class GroupMembershipOut(BaseModel):
    id: UUID
    group_id: UUID
    club_membership_id: UUID
    valid_from: datetime
    valid_to: Optional[datetime]
    membership_status: str
    created_at: datetime
    updated_at: datetime


class GroupInstructorAssignmentCreateRequest(BaseModel):
    user_id: UUID
    role_in_group: str
    is_primary: bool = False
    valid_from: datetime
    valid_to: Optional[datetime] = None


class GroupInstructorAssignmentOut(BaseModel):
    id: UUID
    group_id: UUID
    user_id: UUID
    role_in_group: str
    is_primary: bool
    valid_from: datetime
    valid_to: Optional[datetime]
    created_at: datetime
    updated_at: datetime
