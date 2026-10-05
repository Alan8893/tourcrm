"""Achievement Domain application service (Issue #220).

Canonical source: docs/04-modules/achievements-and-norms.md (A1-A16).
Callers (the API layer) have already checked authorization
(app.achievements.authorization); this module never does.

## Definitions (A1, A3, A14)

Created `inactive`; activated/deactivated explicitly, any number of
times. Only `name`/`description` are editable afterwards: `code`,
`source`, `award_method` and `repeatability` are the Definition's
identity/semantics and are fixed at creation (a changed semantics is a
new Definition, a changed Rule a new Rule Version). Never deleted.
Lifecycle changes never touch Awards.

## Rule Versions (A4, A5, A8, A11, A13)

Each Rule Version belongs to one Definition and is numbered per
Definition. Its condition tree is validated against the approved metric
catalog on creation — a tree with an unsupported metric is rejected.
`source = fstr` requires a Normative Requirement Set Version reference.
A Rule Version is immutable from creation (A13): there is no operation
that changes its condition or normative reference, used or not — a
changed rule is a new Rule Version. Only its lifecycle status changes:
activating a Rule Version deactivates the Definition's previously active
one, so exactly one is current. Changing Rule Versions never touches
Awards.

## Normative Requirement Sets (§4, §5)

A set is a named source; each version carries the §4 metadata. Versions
are created `inactive` — nothing is seeded or activated automatically.
A version's content is immutable once an Award references it.

## Awards (A2, A3, A9, A12, A15)

- Manual issuance requires an `active` Definition whose `award_method`
  is `manual` or `both`, a Member recipient, and — for `non_repeatable` —
  no earlier Award of that Definition for that Person in any state. The
  Award is recorded as `award_method = manual` with the issuing
  Administrator. Its Rule Version provenance is explicit (A15): only a
  Rule Version the Administrator names — which must belong to the same
  Definition, in any lifecycle state — is recorded, together with that
  version's own Normative Set Version reference; without one both stay
  NULL. The current active Rule Version is never attached implicitly.
  For FSTR / normative manual verification (an `fstr` Definition, or a
  named Rule Version that references a Normative Set Version) a
  non-blank verification note is required. A manual Award is never
  presented as an Engine calculation.
- Revocation is the single `active -> revoked` transition, requires an
  explicit reason, and keeps the Award and its provenance intact.
"""

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.achievements.conditions import (
    RuleConditionError,
    UnsupportedMetricError,
    validate_condition,
)
from app.achievements.metrics import APPROVED_METRIC_CODES
from app.achievements.recipients import is_member
from app.achievements.vocabulary import (
    AWARD_METHOD_MANUAL,
    AWARD_STATUS_ACTIVE,
    AWARD_STATUS_REVOKED,
    CANONICAL_DEFINITION_AWARD_METHODS,
    CANONICAL_REPEATABILITIES,
    CANONICAL_SOURCES,
    MANUAL_AWARD_METHODS,
    REPEATABILITY_NON_REPEATABLE,
    SOURCE_FSTR,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
)
from app.db.achievements import (
    DEFINITION_CODE_UNIQUE,
    NON_REPEATABLE_AWARD_INDEX,
    NORMATIVE_SET_CODE_UNIQUE,
    AchievementAward,
    AchievementDefinition,
    AchievementNormativeSet,
    AchievementNormativeSetVersion,
    AchievementRuleVersion,
)
from app.db.identity import Person

# --- errors ----------------------------------------------------------------------


class AchievementError(Exception):
    """Base class for this module's typed, expected failures."""

    code = "achievement_error"


class AchievementNotFoundError(AchievementError):
    code = "not_found"

    def __init__(self, label: str) -> None:
        super().__init__(f"{label} not found")
        self.label = label


class InvalidAchievementValueError(AchievementError):
    code = "invalid_value"


class DuplicateCodeError(AchievementError):
    code = "duplicate_code"


class InvalidRuleError(AchievementError):
    """A5/A8: the condition tree is malformed or uses an unsupported
    metric."""

    def __init__(self, exc: RuleConditionError) -> None:
        super().__init__(str(exc))
        self.code = (
            "unsupported_metric" if isinstance(exc, UnsupportedMetricError) else "invalid_rule"
        )
        self.path = exc.path


class NormativeReferenceRequiredError(AchievementError):
    """A4: an `fstr` Rule Version must reference a Normative Set Version."""

    code = "normative_reference_required"


class VersionInUseError(AchievementError):
    """A4/§5: a version already used by an Award is immutable."""

    code = "version_in_use"


class RuleVersionDefinitionMismatchError(AchievementError):
    """A15: a manual Award may reference only a Rule Version of its own
    Definition."""

    code = "rule_version_definition_mismatch"


class VerificationNoteRequiredError(AchievementError):
    """A15: FSTR / normative manual verification must keep explicit
    verification information."""

    code = "verification_note_required"


class DefinitionInactiveError(AchievementError):
    """A1: an inactive Definition creates no new Awards."""

    code = "definition_inactive"


class ManualAwardNotAllowedError(AchievementError):
    """§3: the Definition's `award_method` does not permit manual issuance."""

    code = "manual_award_not_allowed"


class RecipientNotMemberError(AchievementError):
    """A12: only a Member may receive an Achievement."""

    code = "recipient_not_member"


class AlreadyAwardedError(AchievementError):
    """A3: a `non_repeatable` Definition was already awarded to the Person
    (in any Award state)."""

    code = "already_awarded"


class AwardAlreadyRevokedError(AchievementError):
    code = "award_already_revoked"


class RevocationReasonRequiredError(AchievementError):
    code = "revocation_reason_required"


def _constraint_name(exc: IntegrityError) -> Optional[str]:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _required_text(value: Optional[str], label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise InvalidAchievementValueError(f"{label} must not be empty")
    return text


def _optional_text(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text or None


def _commit(session: Session) -> None:
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


# --- usage ------------------------------------------------------------------------


def rule_version_is_used(session: Session, rule_version_id: uuid.UUID) -> bool:
    stmt = sa.select(AchievementAward.id).where(AchievementAward.rule_version_id == rule_version_id)
    return session.execute(stmt.limit(1)).first() is not None


def normative_version_is_used(session: Session, version_id: uuid.UUID) -> bool:
    stmt = sa.select(AchievementAward.id).where(
        AchievementAward.normative_set_version_id == version_id
    )
    return session.execute(stmt.limit(1)).first() is not None


# --- Definitions ------------------------------------------------------------------


def get_definition(
    session: Session, definition_id: uuid.UUID, *, lock: bool = False
) -> AchievementDefinition:
    stmt = sa.select(AchievementDefinition).where(AchievementDefinition.id == definition_id)
    if lock:
        stmt = stmt.with_for_update()
    definition = session.execute(stmt).scalar_one_or_none()
    if definition is None:
        raise AchievementNotFoundError("Achievement definition")
    return definition


def create_definition(
    session: Session,
    *,
    code: str,
    name: str,
    description: Optional[str],
    source: str,
    award_method: str,
    repeatability: str,
) -> AchievementDefinition:
    if source not in CANONICAL_SOURCES:
        raise InvalidAchievementValueError(f"source must be one of {CANONICAL_SOURCES}")
    if award_method not in CANONICAL_DEFINITION_AWARD_METHODS:
        raise InvalidAchievementValueError(
            f"award_method must be one of {CANONICAL_DEFINITION_AWARD_METHODS}"
        )
    if repeatability not in CANONICAL_REPEATABILITIES:
        raise InvalidAchievementValueError(
            f"repeatability must be one of {CANONICAL_REPEATABILITIES}"
        )
    definition = AchievementDefinition(
        code=_required_text(code, "code"),
        name=_required_text(name, "name"),
        description=_optional_text(description),
        source=source,
        award_method=award_method,
        repeatability=repeatability,
        status=STATUS_INACTIVE,
    )
    try:
        session.add(definition)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _constraint_name(exc) == DEFINITION_CODE_UNIQUE:
            raise DuplicateCodeError(f"Achievement code {code!r} already exists") from exc
        raise
    except Exception:
        session.rollback()
        raise
    return definition


def update_definition(
    session: Session,
    *,
    definition_id: uuid.UUID,
    name: Optional[str],
    description: Optional[str],
    description_set: bool,
) -> AchievementDefinition:
    definition = get_definition(session, definition_id, lock=True)
    if name is not None:
        definition.name = _required_text(name, "name")
    if description_set:
        definition.description = _optional_text(description)
    _commit(session)
    return definition


def set_definition_status(
    session: Session, *, definition_id: uuid.UUID, status: str
) -> AchievementDefinition:
    """A1: activate/deactivate. Idempotent; never touches Awards."""
    definition = get_definition(session, definition_id, lock=True)
    definition.status = status
    _commit(session)
    return definition


# --- Normative Requirement Sets -------------------------------------------------


def get_normative_set(session: Session, set_id: uuid.UUID) -> AchievementNormativeSet:
    normative_set = session.get(AchievementNormativeSet, set_id)
    if normative_set is None:
        raise AchievementNotFoundError("Normative requirement set")
    return normative_set


def create_normative_set(
    session: Session, *, code: str, name: str, description: Optional[str]
) -> AchievementNormativeSet:
    normative_set = AchievementNormativeSet(
        code=_required_text(code, "code"),
        name=_required_text(name, "name"),
        description=_optional_text(description),
    )
    try:
        session.add(normative_set)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _constraint_name(exc) == NORMATIVE_SET_CODE_UNIQUE:
            raise DuplicateCodeError(f"Normative set code {code!r} already exists") from exc
        raise
    except Exception:
        session.rollback()
        raise
    return normative_set


def get_normative_version(
    session: Session, version_id: uuid.UUID, *, lock: bool = False
) -> AchievementNormativeSetVersion:
    stmt = sa.select(AchievementNormativeSetVersion).where(
        AchievementNormativeSetVersion.id == version_id
    )
    if lock:
        stmt = stmt.with_for_update()
    version = session.execute(stmt).scalar_one_or_none()
    if version is None:
        raise AchievementNotFoundError("Normative requirement set version")
    return version


def _validate_effective_range(effective_from: date, effective_to: Optional[date]) -> None:
    if effective_to is not None and effective_to < effective_from:
        raise InvalidAchievementValueError("effective_to must not be before effective_from")


def create_normative_version(
    session: Session,
    *,
    set_id: uuid.UUID,
    source_organization: str,
    document_title: str,
    source_url: str,
    document_version: str,
    publication_date: Optional[date],
    effective_from: date,
    effective_to: Optional[date],
    actor_user_id: uuid.UUID,
) -> AchievementNormativeSetVersion:
    normative_set = session.execute(
        sa.select(AchievementNormativeSet)
        .where(AchievementNormativeSet.id == set_id)
        .with_for_update()
    ).scalar_one_or_none()
    if normative_set is None:
        raise AchievementNotFoundError("Normative requirement set")
    _validate_effective_range(effective_from, effective_to)
    next_number = (
        session.execute(
            sa.select(sa.func.max(AchievementNormativeSetVersion.version_number)).where(
                AchievementNormativeSetVersion.normative_set_id == set_id
            )
        ).scalar_one()
        or 0
    ) + 1
    version = AchievementNormativeSetVersion(
        normative_set_id=set_id,
        version_number=next_number,
        source_organization=_required_text(source_organization, "source_organization"),
        document_title=_required_text(document_title, "document_title"),
        source_url=_required_text(source_url, "source_url"),
        document_version=_required_text(document_version, "document_version"),
        publication_date=publication_date,
        effective_from=effective_from,
        effective_to=effective_to,
        status=STATUS_INACTIVE,
        created_by_user_id=actor_user_id,
    )
    session.add(version)
    _commit(session)
    return version


_NORMATIVE_TEXT_FIELDS = ("source_organization", "document_title", "source_url", "document_version")


def update_normative_version(
    session: Session, *, version_id: uuid.UUID, changes: dict[str, Any]
) -> AchievementNormativeSetVersion:
    """Edit an unused version's content (§5: a used version is immutable)."""
    version = get_normative_version(session, version_id, lock=True)
    if normative_version_is_used(session, version.id):
        raise VersionInUseError(
            "This normative version is already used by an Award; create a new version instead"
        )
    for field in _NORMATIVE_TEXT_FIELDS:
        if field in changes:
            setattr(version, field, _required_text(changes[field], field))
    for field in ("publication_date", "effective_from", "effective_to"):
        if field in changes:
            if field == "effective_from" and changes[field] is None:
                raise InvalidAchievementValueError("effective_from is required")
            setattr(version, field, changes[field])
    _validate_effective_range(version.effective_from, version.effective_to)
    _commit(session)
    return version


def set_normative_version_status(
    session: Session, *, version_id: uuid.UUID, status: str
) -> AchievementNormativeSetVersion:
    version = get_normative_version(session, version_id, lock=True)
    version.status = status
    _commit(session)
    return version


# --- Rule Versions ----------------------------------------------------------------


def get_rule_version(
    session: Session, rule_version_id: uuid.UUID, *, lock: bool = False
) -> AchievementRuleVersion:
    stmt = sa.select(AchievementRuleVersion).where(AchievementRuleVersion.id == rule_version_id)
    if lock:
        stmt = stmt.with_for_update()
    rule = session.execute(stmt).scalar_one_or_none()
    if rule is None:
        raise AchievementNotFoundError("Rule version")
    return rule


def _validated_condition(condition: Any) -> dict[str, Any]:
    try:
        validate_condition(condition, approved_metrics=APPROVED_METRIC_CODES)
    except RuleConditionError as exc:
        raise InvalidRuleError(exc) from exc
    assert isinstance(condition, dict)
    return condition


def _validated_normative_reference(
    session: Session, *, definition: AchievementDefinition, version_id: Optional[uuid.UUID]
) -> Optional[uuid.UUID]:
    if version_id is None:
        if definition.source == SOURCE_FSTR:
            raise NormativeReferenceRequiredError(
                "An FSTR rule version must reference a normative requirement set version"
            )
        return None
    get_normative_version(session, version_id)
    return version_id


def create_rule_version(
    session: Session,
    *,
    definition_id: uuid.UUID,
    condition: Any,
    normative_set_version_id: Optional[uuid.UUID],
    actor_user_id: uuid.UUID,
) -> AchievementRuleVersion:
    definition = get_definition(session, definition_id, lock=True)
    validated = _validated_condition(condition)
    normative_id = _validated_normative_reference(
        session, definition=definition, version_id=normative_set_version_id
    )
    next_number = (
        session.execute(
            sa.select(sa.func.max(AchievementRuleVersion.version_number)).where(
                AchievementRuleVersion.definition_id == definition.id
            )
        ).scalar_one()
        or 0
    ) + 1
    rule = AchievementRuleVersion(
        definition_id=definition.id,
        version_number=next_number,
        condition=validated,
        normative_set_version_id=normative_id,
        status=STATUS_INACTIVE,
        created_by_user_id=actor_user_id,
    )
    session.add(rule)
    _commit(session)
    return rule


def set_rule_version_status(
    session: Session, *, rule_version_id: uuid.UUID, status: str
) -> AchievementRuleVersion:
    """A11: activating a Rule Version makes it the Definition's one
    current version (the previous active one becomes inactive)."""
    rule = get_rule_version(session, rule_version_id)
    get_definition(session, rule.definition_id, lock=True)
    rule = get_rule_version(session, rule_version_id, lock=True)
    if status == STATUS_ACTIVE and rule.status != STATUS_ACTIVE:
        session.execute(
            sa.update(AchievementRuleVersion)
            .where(
                AchievementRuleVersion.definition_id == rule.definition_id,
                AchievementRuleVersion.status == STATUS_ACTIVE,
            )
            .values(status=STATUS_INACTIVE)
        )
        session.flush()
    rule.status = status
    _commit(session)
    session.refresh(rule)
    return rule


# --- Awards -----------------------------------------------------------------------


def get_award(session: Session, award_id: uuid.UUID, *, lock: bool = False) -> AchievementAward:
    stmt = sa.select(AchievementAward).where(AchievementAward.id == award_id)
    if lock:
        stmt = stmt.with_for_update()
    award = session.execute(stmt).scalar_one_or_none()
    if award is None:
        raise AchievementNotFoundError("Achievement award")
    return award


def issue_manual_award(
    session: Session,
    *,
    definition_id: uuid.UUID,
    person_id: uuid.UUID,
    rule_version_id: Optional[uuid.UUID],
    verification_note: Optional[str],
    actor_user_id: uuid.UUID,
) -> AchievementAward:
    definition = get_definition(session, definition_id, lock=True)
    if definition.status != STATUS_ACTIVE:
        raise DefinitionInactiveError("An inactive achievement definition creates no new awards")
    if definition.award_method not in MANUAL_AWARD_METHODS:
        raise ManualAwardNotAllowedError(
            "This achievement definition is awarded automatically only"
        )
    if session.get(Person, person_id) is None:
        raise AchievementNotFoundError("Person")
    if not is_member(session, person_id):
        raise RecipientNotMemberError("Only a Member can receive an achievement")
    if definition.repeatability == REPEATABILITY_NON_REPEATABLE:
        existing = session.execute(
            sa.select(AchievementAward.id).where(
                AchievementAward.definition_id == definition.id,
                AchievementAward.person_id == person_id,
            )
        ).first()
        if existing is not None:
            raise AlreadyAwardedError(
                "This non-repeatable achievement was already awarded to this person"
            )

    # A15: only an explicitly named Rule Version — never the current
    # active one by default.
    rule = get_rule_version(session, rule_version_id) if rule_version_id is not None else None
    if rule is not None and rule.definition_id != definition.id:
        raise RuleVersionDefinitionMismatchError(
            "The rule version belongs to a different achievement definition"
        )
    normative_set_version_id = rule.normative_set_version_id if rule is not None else None
    note = _optional_text(verification_note)
    if note is None and (definition.source == SOURCE_FSTR or normative_set_version_id is not None):
        raise VerificationNoteRequiredError(
            "A manual FSTR / normative verification requires a verification note"
        )

    award = AchievementAward(
        definition_id=definition.id,
        definition_repeatability=definition.repeatability,
        person_id=person_id,
        award_method=AWARD_METHOD_MANUAL,
        rule_version_id=rule.id if rule is not None else None,
        normative_set_version_id=normative_set_version_id,
        awarded_by_user_id=actor_user_id,
        verification_note=note,
        status=AWARD_STATUS_ACTIVE,
    )
    try:
        session.add(award)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _constraint_name(exc) == NON_REPEATABLE_AWARD_INDEX:
            raise AlreadyAwardedError(
                "This non-repeatable achievement was already awarded to this person"
            ) from exc
        raise
    except Exception:
        session.rollback()
        raise
    return award


def revoke_award(
    session: Session, *, award_id: uuid.UUID, reason: Optional[str], actor_user_id: uuid.UUID
) -> AchievementAward:
    """A2: `active -> revoked` with an explicit reason; nothing else on the
    Award (or on any canonical fact) changes."""
    text = (reason or "").strip()
    if not text:
        raise RevocationReasonRequiredError("A revocation reason is required")
    award = get_award(session, award_id, lock=True)
    if award.status != AWARD_STATUS_ACTIVE:
        raise AwardAlreadyRevokedError("This award is already revoked")
    award.status = AWARD_STATUS_REVOKED
    award.revoked_at = datetime.now(timezone.utc)
    award.revoked_by_user_id = actor_user_id
    award.revocation_reason = text
    _commit(session)
    return award


__all__ = [
    "AchievementError",
    "AchievementNotFoundError",
    "InvalidAchievementValueError",
    "DuplicateCodeError",
    "InvalidRuleError",
    "NormativeReferenceRequiredError",
    "VersionInUseError",
    "RuleVersionDefinitionMismatchError",
    "VerificationNoteRequiredError",
    "DefinitionInactiveError",
    "ManualAwardNotAllowedError",
    "RecipientNotMemberError",
    "AlreadyAwardedError",
    "AwardAlreadyRevokedError",
    "RevocationReasonRequiredError",
    "rule_version_is_used",
    "normative_version_is_used",
    "get_definition",
    "create_definition",
    "update_definition",
    "set_definition_status",
    "get_normative_set",
    "create_normative_set",
    "get_normative_version",
    "create_normative_version",
    "update_normative_version",
    "set_normative_version_status",
    "get_rule_version",
    "create_rule_version",
    "set_rule_version_status",
    "get_award",
    "issue_manual_award",
    "revoke_award",
]
