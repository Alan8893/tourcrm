"""Request/response models for `POST /api/v1/persons/wizard` (TH-0116,
GitHub Issue #150).

`role_code` is a closed Pydantic `Literal` (ADR-0039 §3's four canonical
codes) so an invalid value is rejected by FastAPI's own request
validation (422) before any handler code runs — never validated by
comparing against a runtime list here.
"""

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.v1.persons_schemas import PersonOut

PersonWizardRoleCode = Literal["admin", "instructor", "member", "guardian"]


class PersonWizardCreateRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    middle_name: Optional[str] = Field(default=None, max_length=255)
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=32)
    address: Optional[str] = None
    role_code: PersonWizardRoleCode
    # Instructor: 0..N. Member: 1..N (enforced server-side, not by list
    # length here — an empty list is syntactically valid; the semantic
    # "member requires at least one" rule lives in app.people.wizard).
    group_ids: list[UUID] = Field(default_factory=list)
    # Guardian: 1..N (same enforcement note as `group_ids`).
    child_person_ids: list[UUID] = Field(default_factory=list)


class PersonWizardCreateResponse(BaseModel):
    person: PersonOut
    # None exactly when the Person has no email yet (a pending-stub
    # account was created — see app.authentication.account_provisioning).
    temporary_credential: Optional[str] = None
