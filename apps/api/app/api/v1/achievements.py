"""Achievements API — /api/v1/achievements (Issue #220).

Canonical source: docs/04-modules/achievements-and-norms.md (A1-A12).

    GET        /achievements/rule-catalog
    GET/POST   /achievements/definitions
    GET/PATCH  /achievements/definitions/{definition_id}
    POST       /achievements/definitions/{definition_id}/activate
    POST       /achievements/definitions/{definition_id}/deactivate
    GET/POST   /achievements/definitions/{definition_id}/rule-versions
    GET/PATCH  /achievements/rule-versions/{rule_version_id}
    POST       /achievements/rule-versions/{rule_version_id}/activate
    POST       /achievements/rule-versions/{rule_version_id}/deactivate
    GET/POST   /achievements/normative-sets
    GET        /achievements/normative-sets/{set_id}
    GET/POST   /achievements/normative-sets/{set_id}/versions
    GET/PATCH  /achievements/normative-versions/{version_id}
    POST       /achievements/normative-versions/{version_id}/activate
    POST       /achievements/normative-versions/{version_id}/deactivate
    GET/POST   /achievements/awards                 (POST = manual Award)
    GET        /achievements/awards/{award_id}
    POST       /achievements/awards/{award_id}/revoke
    POST       /achievements/reconciliation

There is no DELETE anywhere: Definitions, versions and Awards are never
physically removed (A1/A2/A4).

Authorization (A10, app.achievements.authorization), checked before any
record is loaded: `achievement.manage` for Definitions / Rule Versions /
Normative Sets / reconciliation, `achievement.award` for manual issuance
and revocation, `achievement.read` for the Award history. Every
state-changing request requires the CSRF token.
"""

import logging
import uuid
from typing import NoReturn

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.achievements import engine as achievement_engine
from app.achievements import queries
from app.achievements import service as achievement_service
from app.achievements.authorization import require_achievement_permission
from app.achievements.conditions import (
    CANONICAL_COMPARISON_OPERATORS,
    CANONICAL_LOGIC_OPERATORS,
    MAX_DEPTH,
)
from app.achievements.metrics import APPROVED_METRICS
from app.achievements.vocabulary import (
    PERMISSION_AWARD,
    PERMISSION_MANAGE,
    PERMISSION_READ,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
)
from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.achievements_schemas import (
    AwardMethodLiteral,
    AwardOut,
    AwardRevokeRequest,
    AwardStatusLiteral,
    DefinitionCreateRequest,
    DefinitionOut,
    DefinitionUpdateRequest,
    LifecycleStatusLiteral,
    ManualAwardCreateRequest,
    MetricOut,
    NormativeSetCreateRequest,
    NormativeSetOut,
    NormativeVersionCreateRequest,
    NormativeVersionOut,
    NormativeVersionUpdateRequest,
    ReconciliationOut,
    RuleCatalogOut,
    RuleVersionCreateRequest,
    RuleVersionOut,
    RuleVersionUpdateRequest,
    SourceLiteral,
)
from app.db.achievements import (
    AchievementDefinition,
    AchievementNormativeSet,
    AchievementNormativeSetVersion,
    AchievementRuleVersion,
)
from app.db.session import get_db

logger = logging.getLogger("tourcrm.api.achievements")

router = APIRouter(prefix="/achievements", tags=["achievements"])

_STATUS_BY_ERROR_CODE: dict[str, int] = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "invalid_value": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "invalid_rule": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "unsupported_metric": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "normative_reference_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "recipient_not_member": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "revocation_reason_required": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "duplicate_code": status.HTTP_409_CONFLICT,
    "version_in_use": status.HTTP_409_CONFLICT,
    "definition_inactive": status.HTTP_409_CONFLICT,
    "manual_award_not_allowed": status.HTTP_409_CONFLICT,
    "already_awarded": status.HTTP_409_CONFLICT,
    "award_already_revoked": status.HTTP_409_CONFLICT,
}


def _raise(exc: achievement_service.AchievementError) -> NoReturn:
    details: dict[str, object] = {}
    if isinstance(exc, achievement_service.InvalidRuleError):
        details["path"] = exc.path
    raise APIError(
        _STATUS_BY_ERROR_CODE.get(exc.code, status.HTTP_422_UNPROCESSABLE_ENTITY),
        exc.code,
        str(exc),
        details,
    ) from exc


def _require(db: Session, principal: CurrentPrincipal, permission_code: str) -> None:
    require_achievement_permission(db, user_id=principal.user_id, permission_code=permission_code)


def _pagination(page: int, page_size: int, total: int) -> Pagination:
    pages = (total + page_size - 1) // page_size if total else 0
    return Pagination(page=page, page_size=page_size, total=total, pages=pages)


def _definition_out(row: AchievementDefinition) -> DefinitionOut:
    return DefinitionOut.model_validate(row, from_attributes=True)


def _rule_out(row: AchievementRuleVersion, is_used: bool) -> RuleVersionOut:
    return RuleVersionOut(
        id=row.id,
        definition_id=row.definition_id,
        version_number=row.version_number,
        condition=row.condition,
        normative_set_version_id=row.normative_set_version_id,
        status=row.status,  # type: ignore[arg-type]
        is_used=is_used,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _normative_set_out(row: AchievementNormativeSet) -> NormativeSetOut:
    return NormativeSetOut.model_validate(row, from_attributes=True)


def _normative_version_out(
    row: AchievementNormativeSetVersion, is_used: bool
) -> NormativeVersionOut:
    return NormativeVersionOut(
        id=row.id,
        normative_set_id=row.normative_set_id,
        version_number=row.version_number,
        source_organization=row.source_organization,
        document_title=row.document_title,
        source_url=row.source_url,
        document_version=row.document_version,
        publication_date=row.publication_date,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        status=row.status,  # type: ignore[arg-type]
        is_used=is_used,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _award_out(row: queries.AwardRow) -> AwardOut:
    award = row.award
    return AwardOut(
        id=award.id,
        definition_id=award.definition_id,
        definition_code=row.definition_code,
        definition_name=row.definition_name,
        definition_source=row.definition_source,  # type: ignore[arg-type]
        definition_repeatability=award.definition_repeatability,  # type: ignore[arg-type]
        person_id=award.person_id,
        person_name=f"{row.person_last_name} {row.person_first_name}",
        award_method=award.award_method,  # type: ignore[arg-type]
        rule_version_id=award.rule_version_id,
        rule_version_number=row.rule_version_number,
        normative_set_version_id=award.normative_set_version_id,
        normative_version_number=row.normative_version_number,
        awarded_at=award.awarded_at,
        awarded_by_user_id=award.awarded_by_user_id,
        verification_note=award.verification_note,
        evaluation_trigger=award.evaluation_trigger,  # type: ignore[arg-type]
        evaluated_metrics=award.evaluated_metrics,
        status=award.status,  # type: ignore[arg-type]
        revoked_at=award.revoked_at,
        revoked_by_user_id=award.revoked_by_user_id,
        revocation_reason=award.revocation_reason,
    )


def _award_out_by_id(db: Session, award_id: uuid.UUID) -> AwardOut:
    row = queries.get_award_row(db, award_id)
    if row is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "not_found", "Achievement award not found")
    return _award_out(row)


# --- rule catalog ----------------------------------------------------------------


@router.get("/rule-catalog", response_model=RuleCatalogOut)
def get_rule_catalog(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> RuleCatalogOut:
    _require(db, principal, PERMISSION_MANAGE)
    return RuleCatalogOut(
        metrics=[
            MetricOut(code=metric.code, label=metric.label, description=metric.description)
            for metric in APPROVED_METRICS.values()
        ],
        logic_operators=list(CANONICAL_LOGIC_OPERATORS),
        comparison_operators=list(CANONICAL_COMPARISON_OPERATORS),
        max_depth=MAX_DEPTH,
    )


# --- definitions ----------------------------------------------------------------


@router.get("/definitions", response_model=CollectionResponse[DefinitionOut])
def list_definitions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status_: LifecycleStatusLiteral | None = Query(default=None, alias="status"),
    source: SourceLiteral | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[DefinitionOut]:
    _require(db, principal, PERMISSION_MANAGE)
    rows, total = queries.list_definitions(
        db, page=page, page_size=page_size, status=status_, source=source
    )
    return CollectionResponse(
        items=[_definition_out(row) for row in rows],
        pagination=_pagination(page, page_size, total),
    )


@router.post("/definitions", status_code=status.HTTP_201_CREATED, response_model=DefinitionOut)
def create_definition(
    payload: DefinitionCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> DefinitionOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        definition = achievement_service.create_definition(
            db,
            code=payload.code,
            name=payload.name,
            description=payload.description,
            source=payload.source,
            award_method=payload.award_method,
            repeatability=payload.repeatability,
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    logger.info(
        "achievements.definition.created id=%s user_id=%s", definition.id, principal.user_id
    )
    return _definition_out(definition)


@router.get("/definitions/{definition_id}", response_model=DefinitionOut)
def get_definition(
    definition_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> DefinitionOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        return _definition_out(achievement_service.get_definition(db, definition_id))
    except achievement_service.AchievementError as exc:
        _raise(exc)


@router.patch("/definitions/{definition_id}", response_model=DefinitionOut)
def update_definition(
    definition_id: uuid.UUID,
    payload: DefinitionUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> DefinitionOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        definition = achievement_service.update_definition(
            db,
            definition_id=definition_id,
            name=payload.name,
            description=payload.description,
            description_set="description" in payload.model_fields_set,
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    return _definition_out(definition)


def _set_definition_status(
    db: Session, principal: CurrentPrincipal, definition_id: uuid.UUID, new_status: str
) -> DefinitionOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        definition = achievement_service.set_definition_status(
            db, definition_id=definition_id, status=new_status
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    logger.info(
        "achievements.definition.status id=%s status=%s user_id=%s",
        definition_id,
        new_status,
        principal.user_id,
    )
    return _definition_out(definition)


@router.post("/definitions/{definition_id}/activate", response_model=DefinitionOut)
def activate_definition(
    definition_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> DefinitionOut:
    return _set_definition_status(db, principal, definition_id, STATUS_ACTIVE)


@router.post("/definitions/{definition_id}/deactivate", response_model=DefinitionOut)
def deactivate_definition(
    definition_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> DefinitionOut:
    return _set_definition_status(db, principal, definition_id, STATUS_INACTIVE)


# --- rule versions ----------------------------------------------------------------


@router.get(
    "/definitions/{definition_id}/rule-versions", response_model=CollectionResponse[RuleVersionOut]
)
def list_rule_versions(
    definition_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[RuleVersionOut]:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        achievement_service.get_definition(db, definition_id)
    except achievement_service.AchievementError as exc:
        _raise(exc)
    rows = queries.list_rule_versions(db, definition_id=definition_id)
    return CollectionResponse(
        items=[_rule_out(rule, used) for rule, used in rows],
        pagination=_pagination(1, max(len(rows), 1), len(rows)),
    )


@router.post(
    "/definitions/{definition_id}/rule-versions",
    status_code=status.HTTP_201_CREATED,
    response_model=RuleVersionOut,
)
def create_rule_version(
    definition_id: uuid.UUID,
    payload: RuleVersionCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RuleVersionOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        rule = achievement_service.create_rule_version(
            db,
            definition_id=definition_id,
            condition=payload.condition,
            normative_set_version_id=payload.normative_set_version_id,
            actor_user_id=principal.user_id,
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    logger.info("achievements.rule_version.created id=%s user_id=%s", rule.id, principal.user_id)
    return _rule_out(rule, False)


@router.get("/rule-versions/{rule_version_id}", response_model=RuleVersionOut)
def get_rule_version(
    rule_version_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> RuleVersionOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        rule = achievement_service.get_rule_version(db, rule_version_id)
    except achievement_service.AchievementError as exc:
        _raise(exc)
    return _rule_out(rule, achievement_service.rule_version_is_used(db, rule.id))


@router.patch("/rule-versions/{rule_version_id}", response_model=RuleVersionOut)
def update_rule_version(
    rule_version_id: uuid.UUID,
    payload: RuleVersionUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RuleVersionOut:
    _require(db, principal, PERMISSION_MANAGE)
    fields = payload.model_fields_set
    try:
        rule = achievement_service.update_rule_version(
            db,
            rule_version_id=rule_version_id,
            condition=payload.condition,
            condition_set="condition" in fields,
            normative_set_version_id=payload.normative_set_version_id,
            normative_set="normative_set_version_id" in fields,
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    return _rule_out(rule, False)


def _set_rule_status(
    db: Session, principal: CurrentPrincipal, rule_version_id: uuid.UUID, new_status: str
) -> RuleVersionOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        rule = achievement_service.set_rule_version_status(
            db, rule_version_id=rule_version_id, status=new_status
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    return _rule_out(rule, achievement_service.rule_version_is_used(db, rule.id))


@router.post("/rule-versions/{rule_version_id}/activate", response_model=RuleVersionOut)
def activate_rule_version(
    rule_version_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RuleVersionOut:
    return _set_rule_status(db, principal, rule_version_id, STATUS_ACTIVE)


@router.post("/rule-versions/{rule_version_id}/deactivate", response_model=RuleVersionOut)
def deactivate_rule_version(
    rule_version_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RuleVersionOut:
    return _set_rule_status(db, principal, rule_version_id, STATUS_INACTIVE)


# --- normative sets --------------------------------------------------------------


@router.get("/normative-sets", response_model=CollectionResponse[NormativeSetOut])
def list_normative_sets(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[NormativeSetOut]:
    _require(db, principal, PERMISSION_MANAGE)
    rows, total = queries.list_normative_sets(db, page=page, page_size=page_size)
    return CollectionResponse(
        items=[_normative_set_out(row) for row in rows],
        pagination=_pagination(page, page_size, total),
    )


@router.post(
    "/normative-sets", status_code=status.HTTP_201_CREATED, response_model=NormativeSetOut
)
def create_normative_set(
    payload: NormativeSetCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> NormativeSetOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        normative_set = achievement_service.create_normative_set(
            db, code=payload.code, name=payload.name, description=payload.description
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    return _normative_set_out(normative_set)


@router.get("/normative-sets/{set_id}", response_model=NormativeSetOut)
def get_normative_set(
    set_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> NormativeSetOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        return _normative_set_out(achievement_service.get_normative_set(db, set_id))
    except achievement_service.AchievementError as exc:
        _raise(exc)


@router.get(
    "/normative-sets/{set_id}/versions", response_model=CollectionResponse[NormativeVersionOut]
)
def list_normative_versions(
    set_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[NormativeVersionOut]:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        achievement_service.get_normative_set(db, set_id)
    except achievement_service.AchievementError as exc:
        _raise(exc)
    rows = queries.list_normative_versions(db, set_id=set_id)
    return CollectionResponse(
        items=[_normative_version_out(version, used) for version, used in rows],
        pagination=_pagination(1, max(len(rows), 1), len(rows)),
    )


@router.post(
    "/normative-sets/{set_id}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=NormativeVersionOut,
)
def create_normative_version(
    set_id: uuid.UUID,
    payload: NormativeVersionCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> NormativeVersionOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        version = achievement_service.create_normative_version(
            db,
            set_id=set_id,
            source_organization=payload.source_organization,
            document_title=payload.document_title,
            source_url=payload.source_url,
            document_version=payload.document_version,
            publication_date=payload.publication_date,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            actor_user_id=principal.user_id,
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    return _normative_version_out(version, False)


@router.get("/normative-versions/{version_id}", response_model=NormativeVersionOut)
def get_normative_version(
    version_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> NormativeVersionOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        version = achievement_service.get_normative_version(db, version_id)
    except achievement_service.AchievementError as exc:
        _raise(exc)
    return _normative_version_out(
        version, achievement_service.normative_version_is_used(db, version.id)
    )


@router.patch("/normative-versions/{version_id}", response_model=NormativeVersionOut)
def update_normative_version(
    version_id: uuid.UUID,
    payload: NormativeVersionUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> NormativeVersionOut:
    _require(db, principal, PERMISSION_MANAGE)
    changes = {field: getattr(payload, field) for field in payload.model_fields_set}
    try:
        version = achievement_service.update_normative_version(
            db, version_id=version_id, changes=changes
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    return _normative_version_out(version, False)


def _set_normative_status(
    db: Session, principal: CurrentPrincipal, version_id: uuid.UUID, new_status: str
) -> NormativeVersionOut:
    _require(db, principal, PERMISSION_MANAGE)
    try:
        version = achievement_service.set_normative_version_status(
            db, version_id=version_id, status=new_status
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    return _normative_version_out(
        version, achievement_service.normative_version_is_used(db, version.id)
    )


@router.post("/normative-versions/{version_id}/activate", response_model=NormativeVersionOut)
def activate_normative_version(
    version_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> NormativeVersionOut:
    return _set_normative_status(db, principal, version_id, STATUS_ACTIVE)


@router.post("/normative-versions/{version_id}/deactivate", response_model=NormativeVersionOut)
def deactivate_normative_version(
    version_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> NormativeVersionOut:
    return _set_normative_status(db, principal, version_id, STATUS_INACTIVE)


# --- awards -------------------------------------------------------------------------


@router.get("/awards", response_model=CollectionResponse[AwardOut])
def list_awards(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    definition_id: uuid.UUID | None = Query(default=None),
    person_id: uuid.UUID | None = Query(default=None),
    status_: AwardStatusLiteral | None = Query(default=None, alias="status"),
    award_method: AwardMethodLiteral | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[AwardOut]:
    _require(db, principal, PERMISSION_READ)
    rows, total = queries.list_awards(
        db,
        page=page,
        page_size=page_size,
        definition_id=definition_id,
        person_id=person_id,
        status=status_,
        award_method=award_method,
    )
    return CollectionResponse(
        items=[_award_out(row) for row in rows], pagination=_pagination(page, page_size, total)
    )


@router.post("/awards", status_code=status.HTTP_201_CREATED, response_model=AwardOut)
def create_manual_award(
    payload: ManualAwardCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> AwardOut:
    _require(db, principal, PERMISSION_AWARD)
    try:
        award = achievement_service.issue_manual_award(
            db,
            definition_id=payload.definition_id,
            person_id=payload.person_id,
            verification_note=payload.verification_note,
            actor_user_id=principal.user_id,
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    logger.info(
        "achievements.award.manual id=%s definition_id=%s person_id=%s user_id=%s",
        award.id,
        award.definition_id,
        award.person_id,
        principal.user_id,
    )
    return _award_out_by_id(db, award.id)


@router.get("/awards/{award_id}", response_model=AwardOut)
def get_award(
    award_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> AwardOut:
    _require(db, principal, PERMISSION_READ)
    return _award_out_by_id(db, award_id)


@router.post("/awards/{award_id}/revoke", response_model=AwardOut)
def revoke_award(
    award_id: uuid.UUID,
    payload: AwardRevokeRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> AwardOut:
    _require(db, principal, PERMISSION_AWARD)
    try:
        award = achievement_service.revoke_award(
            db, award_id=award_id, reason=payload.reason, actor_user_id=principal.user_id
        )
    except achievement_service.AchievementError as exc:
        _raise(exc)
    logger.info("achievements.award.revoked id=%s user_id=%s", award.id, principal.user_id)
    return _award_out_by_id(db, award.id)


# --- reconciliation ---------------------------------------------------------------


@router.post("/reconciliation", response_model=ReconciliationOut)
def run_reconciliation(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> ReconciliationOut:
    _require(db, principal, PERMISSION_MANAGE)
    result = achievement_engine.reconcile(db)
    logger.info(
        "achievements.reconciliation.run created=%s user_id=%s",
        result.awards_created,
        principal.user_id,
    )
    return ReconciliationOut(
        evaluated_people=result.evaluated_people,
        evaluated_rules=result.evaluated_rules,
        awards_created=result.awards_created,
    )
