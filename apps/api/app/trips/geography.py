"""Country and Region catalogs and Trip Geography (Issue #271).

Canonical source: docs/04-modules/trips-and-tourist-profile.md §9
(Issue #258, PR #270).

- Country: `code` is the stable ISO 3166-1 alpha-2 code (normalized to
  upper case), `name` the canonical Russian display name. The standard
  ISO 3166-1 set is loaded by the migration; Administrators may maintain
  it.
- Region: belongs to exactly one Country; `code` is unique within that
  Country; `semantic_type` is one of the approved Region semantic types
  (only `administrative_subject`), set at creation and never edited.
- Historical semantic immutability (§9): once a Trip first references a
  Country or Region (`first_used_at`, set by `mark_geography_used` in the
  Trip's own transaction), its semantic fields can no longer change
  through ordinary editing — Country `code`/`name`, Region `code`/`name`/
  `country_id`. Before first use Administrators may edit them. `active`
  and provenance are not semantic fields and stay editable. Changing the
  semantics of a used entry is the future correction/versioning
  workflow. Moving a referenced Region to another Country is rejected by
  the database as well (composite Trip FK, ON UPDATE RESTRICT).
- No physical deletion: the lifecycle is deactivate/reactivate, and
  deactivation never touches Trips that already reference the entry.
- `source_type`/`source_reference` are the entry's provenance — free
  text, both optional — not a generic Provenance subsystem.
- `resolve_trip_geography` is the one backend check used when a Trip's
  Geography is assigned: newly assigned entries must exist and be
  active, a Region requires the Trip's Country and must belong to it.
  Geography is never derived from any other fact.

Callers have already checked authorization
(app.trips.geography_authorization); this module never does.
"""

import re
import uuid
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.trips import (
    COUNTRY_CODE_UNIQUE,
    REGION_COUNTRY_CODE_UNIQUE,
    REGION_SEMANTIC_TYPES,
    TRIP_REGION_FK,
    Country,
    Region,
    Trip,
)

_ISO_ALPHA2 = re.compile(r"^[A-Z]{2}$")


class _Unset:
    """Marker for "field not given" in partial updates (None clears)."""


UNSET = _Unset()


class GeographyError(Exception):
    """Base class for this module's typed, expected failures."""


class CountryNotFoundError(GeographyError):
    def __init__(self, country_id: uuid.UUID) -> None:
        super().__init__(f"Country {country_id} not found")
        self.country_id = country_id


class CountryInactiveError(GeographyError):
    """An inactive Country cannot be assigned to a new or edited Trip."""

    def __init__(self, country_id: uuid.UUID) -> None:
        super().__init__(f"Country {country_id} is inactive")
        self.country_id = country_id


class CountryCodeConflictError(GeographyError):
    def __init__(self, code: str) -> None:
        super().__init__(f"Country code {code!r} already exists")
        self.code = code


class RegionNotFoundError(GeographyError):
    def __init__(self, region_id: uuid.UUID) -> None:
        super().__init__(f"Region {region_id} not found")
        self.region_id = region_id


class RegionInactiveError(GeographyError):
    """An inactive Region cannot be assigned to a new or edited Trip."""

    def __init__(self, region_id: uuid.UUID) -> None:
        super().__init__(f"Region {region_id} is inactive")
        self.region_id = region_id


class RegionCodeConflictError(GeographyError):
    def __init__(self, code: str) -> None:
        super().__init__(f"Region code {code!r} already exists in this country")
        self.code = code


class CountryInUseError(GeographyError):
    """The Country has been used by a Trip: its semantic fields (`code`,
    `name`) are immutable by ordinary editing."""

    def __init__(self, country_id: uuid.UUID, fields: tuple[str, ...]) -> None:
        super().__init__(
            f"Country {country_id} has been used by a Trip; {', '.join(fields)} cannot be changed"
        )
        self.country_id = country_id
        self.fields = fields


class RegionInUseError(GeographyError):
    """The Region has been used by a Trip: its semantic fields (`code`,
    `name`, `country_id`) are immutable by ordinary editing."""

    def __init__(self, region_id: uuid.UUID, fields: tuple[str, ...] = ("country_id",)) -> None:
        super().__init__(
            f"Region {region_id} has been used by a Trip; {', '.join(fields)} cannot be changed"
        )
        self.region_id = region_id
        self.fields = fields


class RegionCountryMismatchError(GeographyError):
    """The Region does not belong to the Trip's Country (or the Trip has
    no Country)."""

    def __init__(self, region_id: uuid.UUID, country_id: Optional[uuid.UUID]) -> None:
        if country_id is None:
            message = f"Region {region_id} requires the Trip's country"
        else:
            message = f"Region {region_id} does not belong to country {country_id}"
        super().__init__(message)
        self.region_id = region_id
        self.country_id = country_id


class InvalidGeographyValueError(GeographyError):
    pass


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise InvalidGeographyValueError(f"{label} must not be empty")
    return text


def _optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip() or None


def _country_code(value: str) -> str:
    code = value.strip().upper()
    if not _ISO_ALPHA2.match(code):
        raise InvalidGeographyValueError("code must be an ISO 3166-1 alpha-2 code")
    return code


def _constraint_name(exc: IntegrityError) -> Optional[str]:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _commit(session: Session, *, code: str, region_id: Optional[uuid.UUID] = None) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        name = _constraint_name(exc)
        if name == COUNTRY_CODE_UNIQUE:
            raise CountryCodeConflictError(code) from exc
        if name == REGION_COUNTRY_CODE_UNIQUE:
            raise RegionCodeConflictError(code) from exc
        if name == TRIP_REGION_FK and region_id is not None:
            raise RegionInUseError(region_id) from exc
        raise
    except Exception:
        session.rollback()
        raise


def _page(session: Session, stmt, order_by, *, page: int, page_size: int):  # type: ignore[no-untyped-def]
    total = session.execute(sa.select(sa.func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        session.execute(stmt.order_by(*order_by).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return list(rows), total


# --- Country -------------------------------------------------------------------------


def get_country(session: Session, country_id: uuid.UUID, *, lock: bool = False) -> Country:
    stmt = sa.select(Country).where(Country.id == country_id)
    if lock:
        stmt = stmt.with_for_update()
    country = session.execute(stmt).scalar_one_or_none()
    if country is None:
        raise CountryNotFoundError(country_id)
    return country


def list_countries(
    session: Session,
    *,
    page: int,
    page_size: int,
    active: Optional[bool] = None,
) -> tuple[list[Country], int]:
    stmt = sa.select(Country)
    if active is not None:
        stmt = stmt.where(Country.active.is_(active))
    return _page(session, stmt, (Country.name, Country.id), page=page, page_size=page_size)


def create_country(
    session: Session,
    *,
    code: str,
    name: str,
    source_type: Optional[str] = None,
    source_reference: Optional[str] = None,
) -> Country:
    country = Country(
        code=_country_code(code),
        name=_required_text(name, "name"),
        active=True,
        source_type=_optional_text(source_type),
        source_reference=_optional_text(source_reference),
    )
    session.add(country)
    _commit(session, code=country.code)
    return country


def update_country(
    session: Session,
    *,
    country_id: uuid.UUID,
    code: Optional[str] = None,
    name: Optional[str] = None,
    source_type: "Optional[str] | _Unset" = UNSET,
    source_reference: "Optional[str] | _Unset" = UNSET,
) -> Country:
    country = get_country(session, country_id, lock=True)
    new_code = _country_code(code) if code is not None else country.code
    new_name = _required_text(name, "name") if name is not None else country.name
    changed = tuple(
        field
        for field, old, new in (("code", country.code, new_code), ("name", country.name, new_name))
        if old != new
    )
    if changed and _country_is_used(session, country):
        raise CountryInUseError(country_id, changed)
    country.code, country.name = new_code, new_name
    if not isinstance(source_type, _Unset):
        country.source_type = _optional_text(source_type)
    if not isinstance(source_reference, _Unset):
        country.source_reference = _optional_text(source_reference)
    _commit(session, code=country.code)
    return country


def set_country_active(session: Session, *, country_id: uuid.UUID, active: bool) -> Country:
    """Activate/deactivate (idempotent). Never touches referencing Trips
    or the Country's Regions."""
    country = get_country(session, country_id, lock=True)
    country.active = active
    _commit(session, code=country.code)
    return country


# --- Region --------------------------------------------------------------------------


def get_region(session: Session, region_id: uuid.UUID, *, lock: bool = False) -> Region:
    stmt = sa.select(Region).where(Region.id == region_id)
    if lock:
        stmt = stmt.with_for_update()
    region = session.execute(stmt).scalar_one_or_none()
    if region is None:
        raise RegionNotFoundError(region_id)
    return region


def list_regions(
    session: Session,
    *,
    page: int,
    page_size: int,
    country_id: Optional[uuid.UUID] = None,
    active: Optional[bool] = None,
) -> tuple[list[Region], int]:
    stmt = sa.select(Region)
    if country_id is not None:
        stmt = stmt.where(Region.country_id == country_id)
    if active is not None:
        stmt = stmt.where(Region.active.is_(active))
    return _page(session, stmt, (Region.name, Region.id), page=page, page_size=page_size)


def create_region(
    session: Session,
    *,
    country_id: uuid.UUID,
    code: str,
    name: str,
    semantic_type: str,
    source_type: Optional[str] = None,
    source_reference: Optional[str] = None,
) -> Region:
    if semantic_type not in REGION_SEMANTIC_TYPES:
        raise InvalidGeographyValueError(f"Unknown region semantic type {semantic_type!r}")
    # FOR SHARE: the Country cannot disappear before this commits.
    if (
        session.execute(
            sa.select(Country.id).where(Country.id == country_id).with_for_update(read=True)
        ).scalar_one_or_none()
        is None
    ):
        raise CountryNotFoundError(country_id)
    region = Region(
        country_id=country_id,
        code=_required_text(code, "code"),
        name=_required_text(name, "name"),
        semantic_type=semantic_type,
        active=True,
        source_type=_optional_text(source_type),
        source_reference=_optional_text(source_reference),
    )
    session.add(region)
    _commit(session, code=region.code)
    return region


def _region_is_used(session: Session, region: Region) -> bool:
    if region.first_used_at is not None:
        return True
    stmt = sa.select(Trip.event_id).where(Trip.region_id == region.id).limit(1)
    return session.execute(stmt).first() is not None


def update_region(
    session: Session,
    *,
    region_id: uuid.UUID,
    country_id: Optional[uuid.UUID] = None,
    code: Optional[str] = None,
    name: Optional[str] = None,
    source_type: "Optional[str] | _Unset" = UNSET,
    source_reference: "Optional[str] | _Unset" = UNSET,
) -> Region:
    region = get_region(session, region_id, lock=True)
    new_country_id = country_id if country_id is not None else region.country_id
    new_code = _required_text(code, "code") if code is not None else region.code
    new_name = _required_text(name, "name") if name is not None else region.name
    changed = tuple(
        field
        for field, old, new in (
            ("code", region.code, new_code),
            ("name", region.name, new_name),
            ("country_id", region.country_id, new_country_id),
        )
        if old != new
    )
    if changed and _region_is_used(session, region):
        raise RegionInUseError(region_id, changed)
    if new_country_id != region.country_id:
        get_country(session, new_country_id)
    region.country_id, region.code, region.name = new_country_id, new_code, new_name
    if not isinstance(source_type, _Unset):
        region.source_type = _optional_text(source_type)
    if not isinstance(source_reference, _Unset):
        region.source_reference = _optional_text(source_reference)
    # The composite Trip FK (ON UPDATE RESTRICT) is the backstop for a Trip
    # referencing the Region concurrently.
    _commit(session, code=region.code, region_id=region_id)
    return region


def set_region_active(session: Session, *, region_id: uuid.UUID, active: bool) -> Region:
    """Activate/deactivate (idempotent). Never touches referencing Trips."""
    region = get_region(session, region_id, lock=True)
    region.active = active
    _commit(session, code=region.code)
    return region


# --- Trip Geography ------------------------------------------------------------------


def _country_is_used(session: Session, country: Country) -> bool:
    if country.first_used_at is not None:
        return True
    stmt = sa.select(Trip.event_id).where(Trip.country_id == country.id).limit(1)
    return session.execute(stmt).first() is not None


def mark_geography_used(
    session: Session, *, country_id: Optional[uuid.UUID], region_id: Optional[uuid.UUID]
) -> None:
    """Record the first use of the Trip's Country/Region (no commit — part
    of the caller's Trip transaction). Never cleared afterwards."""
    now = sa.func.now()
    if country_id is not None:
        session.execute(
            sa.update(Country)
            .where(Country.id == country_id, Country.first_used_at.is_(None))
            .values(first_used_at=now)
        )
    if region_id is not None:
        session.execute(
            sa.update(Region)
            .where(Region.id == region_id, Region.first_used_at.is_(None))
            .values(first_used_at=now)
        )


def resolve_trip_geography(
    session: Session,
    *,
    country_id: Optional[uuid.UUID],
    region_id: Optional[uuid.UUID],
    current_country_id: Optional[uuid.UUID] = None,
    current_region_id: Optional[uuid.UUID] = None,
) -> None:
    """Validate the Geography a Trip is about to hold. A reference that is
    newly assigned (differs from `current_*`) must exist and be active; a
    Region requires the Country and must belong to it. Entries are read
    with `FOR SHARE` so a concurrent deactivation or Region move cannot
    slip in before the caller's transaction commits the assignment."""
    if country_id is not None:
        country = session.execute(
            sa.select(Country).where(Country.id == country_id).with_for_update(read=True)
        ).scalar_one_or_none()
        if country is None:
            raise CountryNotFoundError(country_id)
        if country_id != current_country_id and not country.active:
            raise CountryInactiveError(country_id)
    if region_id is not None:
        region = session.execute(
            sa.select(Region).where(Region.id == region_id).with_for_update(read=True)
        ).scalar_one_or_none()
        if region is None:
            raise RegionNotFoundError(region_id)
        if region_id != current_region_id and not region.active:
            raise RegionInactiveError(region_id)
        if region.country_id != country_id:
            raise RegionCountryMismatchError(region_id, country_id)


__all__ = [
    "UNSET",
    "GeographyError",
    "CountryNotFoundError",
    "CountryInactiveError",
    "CountryCodeConflictError",
    "RegionNotFoundError",
    "RegionInactiveError",
    "RegionCodeConflictError",
    "CountryInUseError",
    "RegionInUseError",
    "RegionCountryMismatchError",
    "InvalidGeographyValueError",
    "get_country",
    "list_countries",
    "create_country",
    "update_country",
    "set_country_active",
    "get_region",
    "list_regions",
    "create_region",
    "update_region",
    "set_region_active",
    "resolve_trip_geography",
    "mark_geography_used",
]
