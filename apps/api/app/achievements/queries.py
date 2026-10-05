"""Achievement Domain reads (Issue #220).

Authorization is checked by the caller (app.achievements.authorization)
before any of these run. Lists are paginated and deterministically
ordered.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.achievements import (
    AchievementAward,
    AchievementDefinition,
    AchievementNormativeSet,
    AchievementNormativeSetVersion,
    AchievementRuleVersion,
)
from app.db.identity import Person


def _page(
    session: Session, stmt: sa.Select, *, order_by: tuple, page: int, page_size: int
) -> tuple[list, int]:
    total = session.execute(sa.select(sa.func.count()).select_from(stmt.subquery())).scalar_one()
    rows = session.execute(
        stmt.order_by(*order_by).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return list(rows), total


def list_definitions(
    session: Session,
    *,
    page: int,
    page_size: int,
    status: Optional[str] = None,
    source: Optional[str] = None,
) -> tuple[list[AchievementDefinition], int]:
    stmt = sa.select(AchievementDefinition)
    if status is not None:
        stmt = stmt.where(AchievementDefinition.status == status)
    if source is not None:
        stmt = stmt.where(AchievementDefinition.source == source)
    rows, total = _page(
        session,
        stmt,
        order_by=(AchievementDefinition.name, AchievementDefinition.id),
        page=page,
        page_size=page_size,
    )
    return [row[0] for row in rows], total


def _used_ids(session: Session, column: Any, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set(
        session.execute(sa.select(column).where(column.in_(ids)).distinct()).scalars().all()
    )


def list_rule_versions(
    session: Session, *, definition_id: uuid.UUID
) -> list[tuple[AchievementRuleVersion, bool]]:
    """Every Rule Version of the Definition, newest first, with its "used"
    flag (referenced by at least one Award)."""
    rules = list(
        session.execute(
            sa.select(AchievementRuleVersion)
            .where(AchievementRuleVersion.definition_id == definition_id)
            .order_by(AchievementRuleVersion.version_number.desc())
        )
        .scalars()
        .all()
    )
    used = _used_ids(session, AchievementAward.rule_version_id, [rule.id for rule in rules])
    return [(rule, rule.id in used) for rule in rules]


def list_normative_sets(
    session: Session, *, page: int, page_size: int
) -> tuple[list[AchievementNormativeSet], int]:
    rows, total = _page(
        session,
        sa.select(AchievementNormativeSet),
        order_by=(AchievementNormativeSet.name, AchievementNormativeSet.id),
        page=page,
        page_size=page_size,
    )
    return [row[0] for row in rows], total


def list_normative_versions(
    session: Session, *, set_id: uuid.UUID
) -> list[tuple[AchievementNormativeSetVersion, bool]]:
    versions = list(
        session.execute(
            sa.select(AchievementNormativeSetVersion)
            .where(AchievementNormativeSetVersion.normative_set_id == set_id)
            .order_by(AchievementNormativeSetVersion.version_number.desc())
        )
        .scalars()
        .all()
    )
    used = _used_ids(
        session, AchievementAward.normative_set_version_id, [version.id for version in versions]
    )
    return [(version, version.id in used) for version in versions]


@dataclass(frozen=True)
class AwardRow:
    award: AchievementAward
    definition_code: str
    definition_name: str
    definition_source: str
    person_last_name: str
    person_first_name: str
    rule_version_number: Optional[int]
    normative_version_number: Optional[int]


def _award_select() -> sa.Select:
    return (
        sa.select(
            AchievementAward,
            AchievementDefinition.code,
            AchievementDefinition.name,
            AchievementDefinition.source,
            Person.last_name,
            Person.first_name,
            AchievementRuleVersion.version_number,
            AchievementNormativeSetVersion.version_number,
        )
        .join(AchievementDefinition, AchievementDefinition.id == AchievementAward.definition_id)
        .join(Person, Person.id == AchievementAward.person_id)
        .outerjoin(
            AchievementRuleVersion, AchievementRuleVersion.id == AchievementAward.rule_version_id
        )
        .outerjoin(
            AchievementNormativeSetVersion,
            AchievementNormativeSetVersion.id == AchievementAward.normative_set_version_id,
        )
    )


def _award_row(row: sa.Row) -> AwardRow:
    return AwardRow(
        award=row[0],
        definition_code=row[1],
        definition_name=row[2],
        definition_source=row[3],
        person_last_name=row[4],
        person_first_name=row[5],
        rule_version_number=row[6],
        normative_version_number=row[7],
    )


def list_awards(
    session: Session,
    *,
    page: int,
    page_size: int,
    definition_id: Optional[uuid.UUID] = None,
    person_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    award_method: Optional[str] = None,
) -> tuple[list[AwardRow], int]:
    stmt = _award_select()
    if definition_id is not None:
        stmt = stmt.where(AchievementAward.definition_id == definition_id)
    if person_id is not None:
        stmt = stmt.where(AchievementAward.person_id == person_id)
    if status is not None:
        stmt = stmt.where(AchievementAward.status == status)
    if award_method is not None:
        stmt = stmt.where(AchievementAward.award_method == award_method)
    rows, total = _page(
        session,
        stmt,
        order_by=(AchievementAward.awarded_at.desc(), AchievementAward.id),
        page=page,
        page_size=page_size,
    )
    return [_award_row(row) for row in rows], total


def get_award_row(session: Session, award_id: uuid.UUID) -> Optional[AwardRow]:
    row = session.execute(_award_select().where(AchievementAward.id == award_id)).first()
    return _award_row(row) if row is not None else None


__all__ = [
    "AwardRow",
    "list_definitions",
    "list_rule_versions",
    "list_normative_sets",
    "list_normative_versions",
    "list_awards",
    "get_award_row",
]
