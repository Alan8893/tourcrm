"""TourismType catalog (Issue #264).

Canonical source: docs/04-modules/trips-and-tourist-profile.md §3
(Issue #256).

- An extensible reference catalog: `code`, `name`, `active`. No value is
  seeded or hardcoded anywhere — entries exist only because an
  Administrator created them.
- No physical deletion: the lifecycle is deactivate/reactivate.
  Deactivation never touches Trips that already reference the entry.
- `resolve_assignable_tourism_type` is the one backend check used when a
  Trip is given a TourismType: the entry must exist and be active.

Callers have already checked authorization
(app.trips.tourism_type_authorization); this module never does.
"""

import uuid
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.trips import TOURISM_TYPE_CODE_UNIQUE, TourismType


class TourismTypeError(Exception):
    """Base class for this module's typed, expected failures."""


class TourismTypeNotFoundError(TourismTypeError):
    def __init__(self, tourism_type_id: uuid.UUID) -> None:
        super().__init__(f"Tourism type {tourism_type_id} not found")
        self.tourism_type_id = tourism_type_id


class TourismTypeInactiveError(TourismTypeError):
    """An inactive TourismType cannot be assigned to a new or edited Trip."""

    def __init__(self, tourism_type_id: uuid.UUID) -> None:
        super().__init__(f"Tourism type {tourism_type_id} is inactive")
        self.tourism_type_id = tourism_type_id


class TourismTypeCodeConflictError(TourismTypeError):
    def __init__(self, code: str) -> None:
        super().__init__(f"Tourism type code {code!r} already exists")
        self.code = code


class InvalidTourismTypeValueError(TourismTypeError):
    pass


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise InvalidTourismTypeValueError(f"{label} must not be empty")
    return text


def _constraint_name(exc: IntegrityError) -> Optional[str]:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _commit(session: Session, *, code: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _constraint_name(exc) == TOURISM_TYPE_CODE_UNIQUE:
            raise TourismTypeCodeConflictError(code) from exc
        raise
    except Exception:
        session.rollback()
        raise


def get_tourism_type(
    session: Session, tourism_type_id: uuid.UUID, *, lock: bool = False
) -> TourismType:
    stmt = sa.select(TourismType).where(TourismType.id == tourism_type_id)
    if lock:
        stmt = stmt.with_for_update()
    tourism_type = session.execute(stmt).scalar_one_or_none()
    if tourism_type is None:
        raise TourismTypeNotFoundError(tourism_type_id)
    return tourism_type


def list_tourism_types(
    session: Session, *, page: int, page_size: int, active: Optional[bool] = None
) -> tuple[list[TourismType], int]:
    stmt = sa.select(TourismType)
    if active is not None:
        stmt = stmt.where(TourismType.active.is_(active))
    total = session.execute(sa.select(sa.func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        session.execute(
            stmt.order_by(TourismType.name, TourismType.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def create_tourism_type(session: Session, *, code: str, name: str) -> TourismType:
    tourism_type = TourismType(
        code=_required_text(code, "code"), name=_required_text(name, "name"), active=True
    )
    session.add(tourism_type)
    _commit(session, code=tourism_type.code)
    return tourism_type


def update_tourism_type(
    session: Session,
    *,
    tourism_type_id: uuid.UUID,
    code: Optional[str] = None,
    name: Optional[str] = None,
) -> TourismType:
    tourism_type = get_tourism_type(session, tourism_type_id, lock=True)
    if code is not None:
        tourism_type.code = _required_text(code, "code")
    if name is not None:
        tourism_type.name = _required_text(name, "name")
    _commit(session, code=tourism_type.code)
    return tourism_type


def set_tourism_type_active(
    session: Session, *, tourism_type_id: uuid.UUID, active: bool
) -> TourismType:
    """Activate/deactivate (idempotent). Never touches referencing Trips."""
    tourism_type = get_tourism_type(session, tourism_type_id, lock=True)
    tourism_type.active = active
    _commit(session, code=tourism_type.code)
    return tourism_type


def resolve_assignable_tourism_type(session: Session, tourism_type_id: uuid.UUID) -> TourismType:
    """The TourismType a Trip may be given now: it must exist and be
    active. Read with `FOR SHARE` so a concurrent deactivation cannot slip
    in before the caller's transaction commits the assignment."""
    tourism_type = session.execute(
        sa.select(TourismType)
        .where(TourismType.id == tourism_type_id)
        .with_for_update(read=True)
    ).scalar_one_or_none()
    if tourism_type is None:
        raise TourismTypeNotFoundError(tourism_type_id)
    if not tourism_type.active:
        raise TourismTypeInactiveError(tourism_type_id)
    return tourism_type


__all__ = [
    "TourismTypeError",
    "TourismTypeNotFoundError",
    "TourismTypeInactiveError",
    "TourismTypeCodeConflictError",
    "InvalidTourismTypeValueError",
    "get_tourism_type",
    "list_tourism_types",
    "create_tourism_type",
    "update_tourism_type",
    "set_tourism_type_active",
    "resolve_assignable_tourism_type",
]
