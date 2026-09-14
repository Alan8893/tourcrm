"""GuardianRelationship/children list query composition (Issue #64).

Applies deterministic scope-based authorization directly inside the SQL
query — never fetch-then-filter-in-Python — mirroring
app.people.queries/app.events.queries exactly, so an unauthorized row
can never leak through pagination/totals/offsets.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.identity import GuardianRelationship, Person
from app.people.guardian_authorization import (
    children_visibility_filter,
    guardian_relationship_visibility_filter,
)

_GUARDIAN_RELATIONSHIP_SORT_COLUMNS: dict[str, sa.UnaryExpression] = {
    "valid_from": GuardianRelationship.valid_from.asc(),
    "-valid_from": GuardianRelationship.valid_from.desc(),
}
GUARDIAN_RELATIONSHIP_DEFAULT_SORT = "-valid_from"


class InvalidSortError(ValueError):
    """`sort` is not one of the whitelisted values."""


def list_guardian_relationships_for_child(
    session: Session,
    *,
    person_id: uuid.UUID,
    user_id: uuid.UUID,
    permission_code: str,
    page: int,
    page_size: int,
    sort: str = GUARDIAN_RELATIONSHIP_DEFAULT_SORT,
) -> tuple[list[GuardianRelationship], int]:
    """Guardians of `person_id` (child-side view — Issue #64 §7), filtered
    to the rows the acting user is authorized to see."""
    if sort not in _GUARDIAN_RELATIONSHIP_SORT_COLUMNS:
        raise InvalidSortError(sort)

    conditions: list[sa.ColumnElement[bool]] = [
        GuardianRelationship.child_person_id == person_id,
        guardian_relationship_visibility_filter(
            session, user_id=user_id, permission_code=permission_code
        ),
    ]

    total = session.execute(
        sa.select(sa.func.count()).select_from(GuardianRelationship).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            sa.select(GuardianRelationship)
            .where(*conditions)
            .order_by(_GUARDIAN_RELATIONSHIP_SORT_COLUMNS[sort])
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def list_children_for_guardian(
    session: Session, *, user_id: uuid.UUID, permission_code: str
) -> list[Person]:
    """Persons for whom the authenticated user (via their own Person) has
    an active GuardianRelationship as guardian — `GET /me/children`
    (Issue #64 §7). Not paginated: Issue #64 does not define pagination
    for this endpoint, and a guardian's own children are an inherently
    small, bounded set.
    """
    predicate = children_visibility_filter(
        session, user_id=user_id, permission_code=permission_code
    )
    rows = (
        session.execute(sa.select(Person).where(predicate).order_by(Person.last_name.asc()))
        .scalars()
        .all()
    )
    return list(rows)


__all__ = [
    "InvalidSortError",
    "GUARDIAN_RELATIONSHIP_DEFAULT_SORT",
    "list_guardian_relationships_for_child",
    "list_children_for_guardian",
]
