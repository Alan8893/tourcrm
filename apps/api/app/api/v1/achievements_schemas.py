"""Request/response models for /api/v1/achievements (Issue #220).

Single resources are returned directly, collections in the canonical
`{items, pagination}` envelope (ADR-0014). Vocabulary fields are typed
as Literals from app.achievements.vocabulary; the Rule `condition` is
accepted as plain JSON and validated by the domain
(app.achievements.conditions), which owns the approved metric catalog.
"""

from datetime import date, datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

SourceLiteral = Literal["club", "fstr"]
DefinitionAwardMethodLiteral = Literal["automatic", "manual", "both"]
AwardMethodLiteral = Literal["automatic", "manual"]
RepeatabilityLiteral = Literal["non_repeatable", "repeatable"]
LifecycleStatusLiteral = Literal["active", "inactive"]
AwardStatusLiteral = Literal["active", "revoked"]
EvaluationTriggerLiteral = Literal["event", "reconciliation"]


# --- metric catalog -----------------------------------------------------------------


class MetricOut(BaseModel):
    code: str
    label: str
    description: str


class RuleCatalogOut(BaseModel):
    """The approved metric catalog (A8) and the condition vocabulary (A5)
    — the UI builds rules only from what the backend declares here."""

    metrics: list[MetricOut]
    logic_operators: list[str]
    comparison_operators: list[str]
    max_depth: int


# --- definitions ----------------------------------------------------------------


class DefinitionCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    source: SourceLiteral
    award_method: DefinitionAwardMethodLiteral
    repeatability: RepeatabilityLiteral


class DefinitionUpdateRequest(BaseModel):
    """Only the descriptive fields; semantics are fixed at creation."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None


class DefinitionOut(BaseModel):
    id: UUID
    code: str
    name: str
    description: Optional[str]
    source: SourceLiteral
    award_method: DefinitionAwardMethodLiteral
    repeatability: RepeatabilityLiteral
    status: LifecycleStatusLiteral
    created_at: datetime
    updated_at: datetime


# --- rule versions ----------------------------------------------------------------


class RuleVersionCreateRequest(BaseModel):
    condition: dict[str, Any]
    normative_set_version_id: Optional[UUID] = None


class RuleVersionUpdateRequest(BaseModel):
    condition: Optional[dict[str, Any]] = None
    normative_set_version_id: Optional[UUID] = None


class RuleVersionOut(BaseModel):
    id: UUID
    definition_id: UUID
    version_number: int
    condition: dict[str, Any]
    normative_set_version_id: Optional[UUID]
    status: LifecycleStatusLiteral
    is_used: bool
    created_by_user_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime


# --- normative sets --------------------------------------------------------------


class NormativeSetCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None


class NormativeSetOut(BaseModel):
    id: UUID
    code: str
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


class NormativeVersionCreateRequest(BaseModel):
    source_organization: str = Field(min_length=1, max_length=255)
    document_title: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    document_version: str = Field(min_length=1, max_length=255)
    publication_date: Optional[date] = None
    effective_from: date
    effective_to: Optional[date] = None


class NormativeVersionUpdateRequest(BaseModel):
    source_organization: Optional[str] = Field(default=None, min_length=1, max_length=255)
    document_title: Optional[str] = Field(default=None, min_length=1)
    source_url: Optional[str] = Field(default=None, min_length=1)
    document_version: Optional[str] = Field(default=None, min_length=1, max_length=255)
    publication_date: Optional[date] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None


class NormativeVersionOut(BaseModel):
    id: UUID
    normative_set_id: UUID
    version_number: int
    source_organization: str
    document_title: str
    source_url: str
    document_version: str
    publication_date: Optional[date]
    effective_from: date
    effective_to: Optional[date]
    status: LifecycleStatusLiteral
    is_used: bool
    created_by_user_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime


# --- awards -------------------------------------------------------------------------


class ManualAwardCreateRequest(BaseModel):
    definition_id: UUID
    person_id: UUID
    verification_note: Optional[str] = None


class AwardRevokeRequest(BaseModel):
    reason: str


class AwardOut(BaseModel):
    id: UUID
    definition_id: UUID
    definition_code: str
    definition_name: str
    definition_source: SourceLiteral
    definition_repeatability: RepeatabilityLiteral
    person_id: UUID
    person_name: str
    award_method: AwardMethodLiteral
    rule_version_id: Optional[UUID]
    rule_version_number: Optional[int]
    normative_set_version_id: Optional[UUID]
    normative_version_number: Optional[int]
    awarded_at: datetime
    awarded_by_user_id: Optional[UUID]
    verification_note: Optional[str]
    evaluation_trigger: Optional[EvaluationTriggerLiteral]
    evaluated_metrics: Optional[dict[str, Any]]
    status: AwardStatusLiteral
    revoked_at: Optional[datetime]
    revoked_by_user_id: Optional[UUID]
    revocation_reason: Optional[str]


class ReconciliationOut(BaseModel):
    evaluated_people: int
    evaluated_rules: int
    awards_created: int
