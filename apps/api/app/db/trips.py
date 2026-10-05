"""Trip / TripParticipant persistence — the tourism-fact foundation
(Issue #245).

Canonical sources: docs/04-modules/trips-and-tourist-profile.md §2/§8
(`Event(type=trip) -> Trip`, TripParticipant), docs/04-modules/events-
and-schedule.md §22 (`Trip.event_id` references the base Event),
docs/03-architecture/data-model.md §9 (`Event 1:0..1 Trip`), and the
Issue #245 PO/CTO decisions (G1-G9, GAP-A/GAP-B).

Trip is a 1:0..1 extension of an ordinary `Event`; it has no lifecycle
of its own (its lifecycle is the Event's, ADR-0018). Its tourism
attributes so far are the optional TourismType catalog reference (Issue
#264, trips-and-tourist-profile.md §3), the optional Official
Difficulty (Issue #268, §4), the optional Geography — Country and
Region catalog references (Issue #271, §9) — and the Duration
Classification (Issue #274, §10) and the optional Result (Issue #276,
§8); Route is a later slice.

- `trips.event_id` is the primary key. Together with the `event_type`
  column (CHECK `= 'trip'`) it forms the composite FK
  `(event_id, event_type) -> events(id, event_type)` with ON UPDATE /
  ON DELETE RESTRICT: the database itself guarantees BR-TRIP-001 (a Trip
  only for an Event of type `trip`) and rejects changing the type of an
  Event that already has a Trip. `events` holds only ordinary Events —
  EventSeries/EventOccurrence live in their own tables — so a Trip can
  never be attached to a series or an occurrence.

TripParticipant is a 1:0..1 extension of an existing `EventParticipation`
of the Trip's Event — never a second registration source:

- `event_participation_id` is the primary key (at most one
  TripParticipant per EventParticipation);
- `(event_id, event_participation_id) -> event_participations(event_id,
  id)` guarantees the participation belongs to exactly this Trip's
  Event, and `event_id -> trips.event_id` that the Event has a Trip;
- no `person_id` (the Person is the participation's), no
  `registration_status`/`participation_status` (registration stays
  `EventParticipation.registration_status`, ADR-0037);
- `actual_participation` is the confirmed tourism fact. It is not
  Attendance (ADR-0032 stays the separate operational fact) and it is
  never reset or deleted when the registration is cancelled (GAP-A):
  `registration_status = cancelled` with `actual_participation = true`
  is a valid historical state. EventParticipation rows are never
  deleted, and every FK here is RESTRICT — no cascade exists.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TRIP_EVENT_TYPE = "trip"

# Names used by app.events.crud / app.trips.service to recognize these
# specific violations.
TRIP_EVENT_FK = "fk_trips_event_id_event_type"
TRIP_PRIMARY_KEY = "pk_trips"
TOURISM_TYPE_CODE_UNIQUE = "uq_tourism_types_code"
COUNTRY_CODE_UNIQUE = "uq_countries_code"
REGION_COUNTRY_CODE_UNIQUE = "uq_regions_country_id_code"
# trips-and-tourist-profile.md §9: the approved Region semantic types.
REGION_SEMANTIC_TYPE_ADMINISTRATIVE_SUBJECT = "administrative_subject"
REGION_SEMANTIC_TYPES: tuple[str, ...] = (REGION_SEMANTIC_TYPE_ADMINISTRATIVE_SUBJECT,)
TRIP_REGION_FK = "fk_trips_region_id_country_id"

# Issue #268 (trips-and-tourist-profile.md §4): the approved Official
# Difficulty combinations, enforced by the database as well as by
# app.trips.official_difficulty. All three columns NULL = no Difficulty.
_OFFICIAL_DIFFICULTY_COMBINATIONS_SQL = (
    # COALESCE: a CHECK whose expression is NULL passes, and `value IN
    # (...)`/`mode = ...` are NULL for a NULL column — never let that
    # unknown slip through as "allowed".
    "COALESCE("
    "(official_difficulty_mode IS NULL"
    " AND official_difficulty_value IS NULL AND official_difficulty_source IS NULL)"
    " OR (official_difficulty_mode = 'NONE' AND official_difficulty_value IS NULL)"
    " OR (official_difficulty_mode = 'DEGREE'"
    " AND official_difficulty_value IN ('I', 'II', 'III')"
    " AND official_difficulty_source IS NOT NULL)"
    " OR (official_difficulty_mode = 'CATEGORY'"
    " AND official_difficulty_value IN ('I', 'II', 'III', 'IV', 'V', 'VI')"
    " AND official_difficulty_source IS NOT NULL)"
    " OR (official_difficulty_mode = 'WEEKEND' AND official_difficulty_value IS NULL"
    " AND official_difficulty_source IS NOT NULL),"
    " false)"
)


class TourismType(Base):
    """TourismType reference catalog (Issue #264;
    trips-and-tourist-profile.md §3, Issue #256).

    An extensible catalog: no value is seeded or hardcoded. Entries are
    never physically deleted — the lifecycle is `active` true/false — and
    `trips.tourism_type_id` references them with ON DELETE RESTRICT, so a
    historically referenced entry cannot be removed either."""

    __tablename__ = "tourism_types"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint("code", name=TOURISM_TYPE_CODE_UNIQUE),
        sa.CheckConstraint("length(btrim(code)) > 0", name="ck_tourism_types_code_not_blank"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_tourism_types_name_not_blank"),
    )


class Country(Base):
    """Country reference catalog (Issue #271; trips-and-tourist-profile.md
    §9, Issue #258, PR #270).

    `code` is the stable ISO 3166-1 alpha-2 code (machine identifier),
    `name` the canonical Russian display name. The standard ISO 3166-1
    set is loaded by the migration that creates this table. Entries are
    never physically deleted — the lifecycle is `active` true/false — and
    every reference to them is ON DELETE RESTRICT. `source_type`/
    `source_reference` are the entry's provenance (where the definition
    comes from), not a generic Provenance subsystem. Once a Trip has
    referenced the entry (`first_used_at`), `code` and `name` can no longer
    change through ordinary editing."""

    __tablename__ = "countries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(sa.String(2), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    source_type: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)
    source_reference: Mapped[Optional[str]] = mapped_column(sa.String(500), nullable=True)
    # §9: set when a Trip first references the entry; from then on its
    # semantic fields are immutable by ordinary editing (never cleared).
    first_used_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint("code", name=COUNTRY_CODE_UNIQUE),
        sa.CheckConstraint("code ~ '^[A-Z]{2}$'", name="ck_countries_code_iso_alpha2"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_countries_name_not_blank"),
        sa.CheckConstraint(
            "source_type IS NULL OR length(btrim(source_type)) > 0",
            name="ck_countries_source_type_not_blank",
        ),
        sa.CheckConstraint(
            "source_reference IS NULL OR length(btrim(source_reference)) > 0",
            name="ck_countries_source_reference_not_blank",
        ),
    )


class Region(Base):
    """Region reference catalog (Issue #271; trips-and-tourist-profile.md
    §9). A Region belongs to exactly one Country and carries a semantic
    type; the only approved one is `administrative_subject` (the subjects
    of the Russian Federation). `code` is unique within its Country. `(id, country_id)` is
    unique so a Trip can reference the pair: changing the Country of a
    Region some Trip already references is rejected by the database
    (ON UPDATE RESTRICT), so history never silently changes meaning. Once a
    Trip has referenced the Region (`first_used_at`), `code`, `name` and
    `country_id` can no longer change through ordinary editing."""

    __tablename__ = "regions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    # §9: what kind of Region this is; set at creation, never edited.
    semantic_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    source_type: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True)
    source_reference: Mapped[Optional[str]] = mapped_column(sa.String(500), nullable=True)
    # §9: set when a Trip first references the entry; from then on its
    # semantic fields are immutable by ordinary editing (never cleared).
    first_used_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["country_id"],
            ["countries.id"],
            name="fk_regions_country_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.UniqueConstraint("country_id", "code", name=REGION_COUNTRY_CODE_UNIQUE),
        sa.UniqueConstraint("id", "country_id", name="uq_regions_id_country_id"),
        sa.CheckConstraint("length(btrim(code)) > 0", name="ck_regions_code_not_blank"),
        sa.CheckConstraint(
            "semantic_type IN ('administrative_subject')", name="ck_regions_semantic_type"
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_regions_name_not_blank"),
        sa.CheckConstraint(
            "source_type IS NULL OR length(btrim(source_type)) > 0",
            name="ck_regions_source_type_not_blank",
        ),
        sa.CheckConstraint(
            "source_reference IS NULL OR length(btrim(source_reference)) > 0",
            name="ck_regions_source_reference_not_blank",
        ),
    )


class Trip(Base):
    """The tourism-specific extension of one ordinary `Event(type=trip)`."""

    __tablename__ = "trips"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False, default=TRIP_EVENT_TYPE)
    # Issue #264: 0..1 TourismType — a catalog reference, never free text.
    tourism_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Issue #268: 0..1 Official Difficulty — one structured classification
    # (mode, value for DEGREE/CATEGORY, source). Stored on the Trip row
    # itself, so a Trip can never carry more than one.
    official_difficulty_mode: Mapped[Optional[str]] = mapped_column(sa.String(16), nullable=True)
    official_difficulty_value: Mapped[Optional[str]] = mapped_column(sa.String(8), nullable=True)
    official_difficulty_source: Mapped[Optional[str]] = mapped_column(sa.String(500), nullable=True)
    # Issue #271: 0..1 Country and 0..1 Region — catalog references, never
    # free text. A Region requires the Trip's Country and must belong to it
    # (CHECK + composite FK below).
    country_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    region_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Issue #274: exactly one Duration Classification — `UNCLASSIFIED` until
    # set. A semantic fact, not a second source of the planned interval
    # (that stays Event.start_at/end_at).
    duration_classification: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default="UNCLASSIFIED", server_default="UNCLASSIFIED"
    )
    # Issue #276: 0..1 Result of the Trip itself — not the Event/Trip
    # lifecycle and not a participant's result.
    result: Mapped[Optional[str]] = mapped_column(sa.String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.PrimaryKeyConstraint("event_id", name=TRIP_PRIMARY_KEY),
        sa.CheckConstraint(f"event_type = '{TRIP_EVENT_TYPE}'", name="ck_trips_event_type_trip"),
        sa.ForeignKeyConstraint(
            ["event_id", "event_type"],
            ["events.id", "events.event_type"],
            name=TRIP_EVENT_FK,
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tourism_type_id"],
            ["tourism_types.id"],
            name="fk_trips_tourism_type_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.Index("ix_trips_tourism_type_id", "tourism_type_id"),
        sa.CheckConstraint(
            _OFFICIAL_DIFFICULTY_COMBINATIONS_SQL, name="ck_trips_official_difficulty_combination"
        ),
        sa.CheckConstraint(
            "official_difficulty_source IS NULL OR length(btrim(official_difficulty_source)) > 0",
            name="ck_trips_official_difficulty_source_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["country_id"],
            ["countries.id"],
            name="fk_trips_country_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        # Region must belong to the Trip's Country. With MATCH SIMPLE this
        # FK is only checked when both columns are set — the CHECK below
        # makes a Region without a Country impossible.
        sa.ForeignKeyConstraint(
            ["region_id", "country_id"],
            ["regions.id", "regions.country_id"],
            name=TRIP_REGION_FK,
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint(
            "region_id IS NULL OR country_id IS NOT NULL",
            name="ck_trips_region_requires_country",
        ),
        sa.CheckConstraint(
            "duration_classification IN ('ONE_DAY', 'MULTI_DAY', 'UNCLASSIFIED')",
            name="ck_trips_duration_classification",
        ),
        sa.CheckConstraint(
            "result IS NULL OR result IN ('COMPLETED', 'PARTIALLY_COMPLETED', 'NOT_COMPLETED')",
            name="ck_trips_result",
        ),
        sa.Index("ix_trips_country_id", "country_id"),
        sa.Index("ix_trips_region_id", "region_id"),
    )


class TripParticipant(Base):
    """The tourism-specific extension of one `EventParticipation` of a
    Trip's Event."""

    __tablename__ = "trip_participants"

    event_participation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actual_participation: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.PrimaryKeyConstraint("event_participation_id", name="pk_trip_participants"),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["trips.event_id"],
            name="fk_trip_participants_event_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["event_id", "event_participation_id"],
            ["event_participations.event_id", "event_participations.id"],
            name="fk_trip_participants_event_participation",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.Index("ix_trip_participants_event_id", "event_id"),
    )


__all__ = [
    "TourismType",
    "Country",
    "Region",
    "Trip",
    "TripParticipant",
    "TRIP_EVENT_TYPE",
    "TRIP_EVENT_FK",
    "TRIP_PRIMARY_KEY",
    "TOURISM_TYPE_CODE_UNIQUE",
    "COUNTRY_CODE_UNIQUE",
    "REGION_COUNTRY_CODE_UNIQUE",
    "REGION_SEMANTIC_TYPE_ADMINISTRATIVE_SUBJECT",
    "REGION_SEMANTIC_TYPES",
    "TRIP_REGION_FK",
]
