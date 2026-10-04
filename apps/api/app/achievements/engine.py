"""Achievement Engine (Issue #220, A6/A7/A9/A11/A12).

Canonical source: docs/04-modules/achievements-and-norms.md §11, §21-§28.

    canonical facts + Rule Version -> metric evaluation -> Award

## Which Definitions are evaluated (A1/A9/A11)

Only a Definition that is

- `active` (A1);
- awardable automatically (`award_method` `automatic` or `both`);
- `non_repeatable` (A9 — automatic Awards for `repeatable` Definitions
  are outside the current boundary; they are skipped, never guessed);

and that has exactly one `active` Rule Version (A11; a database
invariant). When that Rule Version references a Normative Requirement
Set Version — always the case for `source = fstr` (A4) — that version
must be `active` and effective on the evaluation date
(`effective_from <= today <= effective_to`, `effective_to` open when
NULL). An `fstr` Rule Version without a normative reference is never
applicable. If no applicable Rule Version exists, nothing is created.

"Affected" Definitions for an event-driven trigger are those whose
applicable Rule references at least one of the changed metrics.

## Who is evaluated (A12)

Only Members (app.achievements.recipients). Administrator, Instructor and
Guardian are never recipients.

## Idempotency and historical safety (A2/A3/A7)

- A Person who already has any Award of a `non_repeatable` Definition —
  active or revoked — is skipped: revocation never re-opens automatic
  awarding.
- The insert itself is `ON CONFLICT DO NOTHING` against
  `uq_achievement_awards_non_repeatable`, so concurrent or repeated
  processing of the same change can never produce a second Award.
- The Engine only inserts. It never updates, revokes or deletes an
  existing Award, and never re-points an Award to a newer Rule Version.

## Triggering (A7)

- Event-driven: `handle_tourism_facts_changed` — called after a canonical
  tourism fact commits (app.achievements.triggers).
- Reconciliation: `reconcile` — re-evaluates every Member against every
  applicable Definition and creates only missing Awards. Run periodically
  via `python -m app.cli.reconcile_achievements`, or on demand by an
  Administrator through the API.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.achievements.conditions import evaluate_condition, referenced_metrics
from app.achievements.metrics import APPROVED_METRIC_CODES, compute_metrics
from app.achievements.recipients import member_person_ids
from app.achievements.vocabulary import (
    AUTOMATIC_AWARD_METHODS,
    AWARD_METHOD_AUTOMATIC,
    AWARD_STATUS_ACTIVE,
    REPEATABILITY_NON_REPEATABLE,
    SOURCE_FSTR,
    STATUS_ACTIVE,
    TRIGGER_EVENT,
    TRIGGER_RECONCILIATION,
)
from app.db.achievements import (
    AchievementAward,
    AchievementDefinition,
    AchievementNormativeSetVersion,
    AchievementRuleVersion,
)

logger = logging.getLogger("tourcrm.achievements.engine")


@dataclass(frozen=True)
class ApplicableRule:
    definition_id: uuid.UUID
    rule_version_id: uuid.UUID
    normative_set_version_id: Optional[uuid.UUID]
    condition: dict[str, Any]
    metrics: frozenset[str]


@dataclass(frozen=True)
class EvaluationResult:
    evaluated_people: int
    evaluated_rules: int
    awards_created: int


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def normative_version_is_applicable(
    version: Optional[AchievementNormativeSetVersion], on: date
) -> bool:
    if version is None:
        return False
    if version.status != STATUS_ACTIVE or version.effective_from > on:
        return False
    return version.effective_to is None or on <= version.effective_to


def applicable_rules(session: Session, *, on: Optional[date] = None) -> list[ApplicableRule]:
    """The applicable active Rule Version of every Definition the
    automatic Engine may award (see module docstring)."""
    evaluation_date = on or today_utc()
    rows = session.execute(
        sa.select(AchievementDefinition, AchievementRuleVersion, AchievementNormativeSetVersion)
        .join(
            AchievementRuleVersion,
            sa.and_(
                AchievementRuleVersion.definition_id == AchievementDefinition.id,
                AchievementRuleVersion.status == STATUS_ACTIVE,
            ),
        )
        .outerjoin(
            AchievementNormativeSetVersion,
            AchievementNormativeSetVersion.id == AchievementRuleVersion.normative_set_version_id,
        )
        .where(
            AchievementDefinition.status == STATUS_ACTIVE,
            AchievementDefinition.award_method.in_(sorted(AUTOMATIC_AWARD_METHODS)),
            AchievementDefinition.repeatability == REPEATABILITY_NON_REPEATABLE,
        )
        .order_by(AchievementDefinition.code)
    ).all()
    result: list[ApplicableRule] = []
    for definition, rule, normative in rows:
        references_normative = rule.normative_set_version_id is not None
        if definition.source == SOURCE_FSTR and not references_normative:
            continue
        if references_normative and not normative_version_is_applicable(
            normative, evaluation_date
        ):
            continue
        metrics = referenced_metrics(rule.condition)
        if not metrics <= APPROVED_METRIC_CODES:
            # Unreachable for a rule stored through the service layer (A8
            # validation on write); never evaluated with a substitute.
            logger.warning(
                "achievements.engine.unsupported_metric rule_version_id=%s", rule.id
            )
            continue
        result.append(
            ApplicableRule(
                definition_id=definition.id,
                rule_version_id=rule.id,
                normative_set_version_id=rule.normative_set_version_id,
                condition=rule.condition,
                metrics=metrics,
            )
        )
    return result


def _people_with_award(
    session: Session, *, definition_id: uuid.UUID, person_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """People who already hold an Award of this Definition, in any state."""
    return set(
        session.execute(
            sa.select(AchievementAward.person_id).where(
                AchievementAward.definition_id == definition_id,
                AchievementAward.person_id.in_(list(person_ids)),
            )
        ).scalars()
    )


def evaluate(
    session: Session,
    *,
    person_ids: Optional[Sequence[uuid.UUID]],
    trigger: str,
    changed_metrics: Optional[frozenset[str]] = None,
    on: Optional[date] = None,
) -> EvaluationResult:
    """Evaluate Members (all of them when `person_ids` is None) against the
    applicable Rules — restricted to Rules referencing `changed_metrics`
    when given — and insert each missing qualified Award. Commits."""
    rules = applicable_rules(session, on=on)
    if changed_metrics is not None:
        rules = [rule for rule in rules if rule.metrics & changed_metrics]
    if not rules:
        return EvaluationResult(evaluated_people=0, evaluated_rules=0, awards_created=0)

    members = member_person_ids(session, person_ids)
    if not members:
        return EvaluationResult(evaluated_people=0, evaluated_rules=len(rules), awards_created=0)

    needed_metrics = frozenset().union(*(rule.metrics for rule in rules))
    facts = compute_metrics(session, metric_codes=needed_metrics, person_ids=members)

    created = 0
    try:
        for rule in rules:
            already_awarded = _people_with_award(
                session, definition_id=rule.definition_id, person_ids=members
            )
            for person_id in members:
                if person_id in already_awarded:
                    continue
                person_facts = facts[person_id]
                if not evaluate_condition(rule.condition, person_facts):
                    continue
                snapshot = {code: person_facts.get(code) for code in sorted(rule.metrics)}
                inserted = session.execute(
                    pg_insert(AchievementAward)
                    .values(
                        id=uuid.uuid4(),
                        definition_id=rule.definition_id,
                        definition_repeatability=REPEATABILITY_NON_REPEATABLE,
                        person_id=person_id,
                        award_method=AWARD_METHOD_AUTOMATIC,
                        rule_version_id=rule.rule_version_id,
                        normative_set_version_id=rule.normative_set_version_id,
                        evaluation_trigger=trigger,
                        evaluated_metrics=snapshot,
                        status=AWARD_STATUS_ACTIVE,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["definition_id", "person_id"],
                        index_where=sa.text(
                            f"definition_repeatability = '{REPEATABILITY_NON_REPEATABLE}'"
                        ),
                    )
                    .returning(AchievementAward.id)
                ).first()
                if inserted is not None:
                    created += 1
                    logger.info(
                        "achievements.engine.awarded definition_id=%s person_id=%s "
                        "rule_version_id=%s trigger=%s",
                        rule.definition_id,
                        person_id,
                        rule.rule_version_id,
                        trigger,
                    )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return EvaluationResult(
        evaluated_people=len(members), evaluated_rules=len(rules), awards_created=created
    )


def handle_tourism_facts_changed(
    session: Session, *, person_ids: Sequence[uuid.UUID], changed_metrics: frozenset[str]
) -> EvaluationResult:
    """Event-driven path: canonical facts of `person_ids` changed."""
    if not person_ids:
        return EvaluationResult(evaluated_people=0, evaluated_rules=0, awards_created=0)
    return evaluate(
        session,
        person_ids=person_ids,
        trigger=TRIGGER_EVENT,
        changed_metrics=changed_metrics,
    )


def reconcile(session: Session) -> EvaluationResult:
    """Reconciliation path: every Member against every applicable Rule."""
    result = evaluate(session, person_ids=None, trigger=TRIGGER_RECONCILIATION)
    logger.info(
        "achievements.engine.reconciled people=%s rules=%s created=%s",
        result.evaluated_people,
        result.evaluated_rules,
        result.awards_created,
    )
    return result


__all__ = [
    "ApplicableRule",
    "EvaluationResult",
    "today_utc",
    "normative_version_is_applicable",
    "applicable_rules",
    "evaluate",
    "handle_tourism_facts_changed",
    "reconcile",
]
