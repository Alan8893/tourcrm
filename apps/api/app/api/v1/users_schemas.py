"""Response model for /api/v1/users (TH-0107).

`GET /users` is a safe, operational user *directory* — not a full admin
User Management API (docs/05-api/endpoint-inventory.md §2 documents more
than this implements; see docs/05-api/users-api.md for exactly what is and
isn't built). `UserDirectoryOut` is deliberately this endpoint's only
response shape: it must never carry `password_hash`,
`login_identifier`/`normalized_login_identifier`, `status`,
`email_verified_at`, `last_login_at`, or any role-assignment data — this
endpoint exists to let a caller pick a person by name, nothing else.
"""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class UserDirectoryOut(BaseModel):
    id: UUID
    person_id: UUID
    first_name: str
    last_name: str
    middle_name: Optional[str]
