"""Official Difficulty of a Trip (Issue #268).

Canonical source: docs/04-modules/trips-and-tourist-profile.md §4
(Issue #257).

A Trip has 0..1 Official Difficulty: one structured classification made
of a mode, a value (only for `DEGREE`/`CATEGORY`) and its `source`.
`DEGREE` and `CATEGORY` are mutually exclusive modes of the same single
classification, never two independent fields. Approved combinations:

- `NONE` — no official classification; no value; `source` optional;
- `DEGREE` — value I, II or III; `source` required;
- `CATEGORY` — value I, II, III, IV, V or VI; `source` required;
- `WEEKEND` — a separate mode, not `degree = 0`; no value; `source`
  required.

`build_official_difficulty` is the one authoritative backend check; the
database repeats it as CHECK constraints (app.db.trips). Applicability
to a TourismType is not approved and is deliberately not checked, and
the classification is never derived from any other fact. `source` is
part of this fact only — not a generic Provenance model.

Callers have already checked authorization
(app.trips.official_difficulty_authorization); this module never does.
"""

from dataclasses import dataclass
from typing import Optional

from app.db.trips import Trip

MODE_NONE = "NONE"
MODE_DEGREE = "DEGREE"
MODE_CATEGORY = "CATEGORY"
MODE_WEEKEND = "WEEKEND"
OFFICIAL_DIFFICULTY_MODES: tuple[str, ...] = (MODE_NONE, MODE_DEGREE, MODE_CATEGORY, MODE_WEEKEND)

DEGREE_VALUES: tuple[str, ...] = ("I", "II", "III")
CATEGORY_VALUES: tuple[str, ...] = ("I", "II", "III", "IV", "V", "VI")
OFFICIAL_DIFFICULTY_VALUES: tuple[str, ...] = CATEGORY_VALUES

SOURCE_MAX_LENGTH = 500

_VALUES_BY_MODE: dict[str, tuple[str, ...]] = {
    MODE_DEGREE: DEGREE_VALUES,
    MODE_CATEGORY: CATEGORY_VALUES,
}
_SOURCE_REQUIRED_MODES: tuple[str, ...] = (MODE_DEGREE, MODE_CATEGORY, MODE_WEEKEND)


@dataclass(frozen=True)
class OfficialDifficulty:
    mode: str
    value: Optional[str]
    source: Optional[str]


class InvalidOfficialDifficultyError(Exception):
    """The mode/value/source combination is not an approved one."""


def build_official_difficulty(
    *, mode: str, value: Optional[str], source: Optional[str]
) -> OfficialDifficulty:
    """Validate one Official Difficulty and return it normalized
    (`source` stripped; a blank `source` counts as absent)."""
    if mode not in OFFICIAL_DIFFICULTY_MODES:
        raise InvalidOfficialDifficultyError(f"Unknown official difficulty mode {mode!r}")

    allowed_values = _VALUES_BY_MODE.get(mode)
    if allowed_values is None:
        if value is not None:
            raise InvalidOfficialDifficultyError(f"Mode {mode} does not take a value")
    elif value is None:
        raise InvalidOfficialDifficultyError(f"Mode {mode} requires a value")
    elif value not in allowed_values:
        raise InvalidOfficialDifficultyError(
            f"{value!r} is not a {mode} value (allowed: {', '.join(allowed_values)})"
        )

    normalized_source = source.strip() if source is not None else None
    if not normalized_source:
        normalized_source = None
    if normalized_source is None and mode in _SOURCE_REQUIRED_MODES:
        raise InvalidOfficialDifficultyError(f"Mode {mode} requires a source")
    if normalized_source is not None and len(normalized_source) > SOURCE_MAX_LENGTH:
        raise InvalidOfficialDifficultyError(
            f"source must be at most {SOURCE_MAX_LENGTH} characters"
        )
    return OfficialDifficulty(mode=mode, value=value, source=normalized_source)


def official_difficulty_of(trip: Trip) -> Optional[OfficialDifficulty]:
    if trip.official_difficulty_mode is None:
        return None
    return OfficialDifficulty(
        mode=trip.official_difficulty_mode,
        value=trip.official_difficulty_value,
        source=trip.official_difficulty_source,
    )


__all__ = [
    "MODE_NONE",
    "MODE_DEGREE",
    "MODE_CATEGORY",
    "MODE_WEEKEND",
    "OFFICIAL_DIFFICULTY_MODES",
    "DEGREE_VALUES",
    "CATEGORY_VALUES",
    "OFFICIAL_DIFFICULTY_VALUES",
    "SOURCE_MAX_LENGTH",
    "OfficialDifficulty",
    "InvalidOfficialDifficultyError",
    "build_official_difficulty",
    "official_difficulty_of",
]
