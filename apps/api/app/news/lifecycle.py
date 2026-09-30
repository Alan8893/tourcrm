"""News lifecycle rules (docs/04-ux/news.md §5; Issue #227 "Lifecycle").

Pure domain validation — no ORM, FastAPI or authorization import.

Transitions:

- `draft -> published` (publish);
- `draft -> archived` and `published -> archived` (archive — the News
  "delete" operation is this soft-delete, never a physical deletion);
- `archived` is terminal: it is "no longer current" and no restore/
  unpublish transition is defined by the canonical contract, so none is
  invented here.

Content (title/body/audience/event link/image) may be edited only while
the News is not archived.
"""

from app.news.vocabulary import (
    ARCHIVED,
    AUDIENCE_CLUB,
    AUDIENCE_GROUPS,
    CANONICAL_NEWS_AUDIENCE_TYPES,
    DRAFT,
    PUBLISHED,
)

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    DRAFT: frozenset({PUBLISHED, ARCHIVED}),
    PUBLISHED: frozenset({ARCHIVED}),
    ARCHIVED: frozenset(),
}

TITLE_MAX_LENGTH = 255
LOCATION_MAX_LENGTH = 255


class NewsDomainError(Exception):
    """Base class for News validation failures."""


class InvalidNewsStatusTransitionError(NewsDomainError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"Cannot change News status from {current!r} to {target!r}")
        self.current = current
        self.target = target


class NewsArchivedError(NewsDomainError):
    def __init__(self) -> None:
        super().__init__("Archived News cannot be modified")


class InvalidNewsContentError(NewsDomainError):
    """Title/body/location missing or out of range."""


class InvalidNewsAudienceError(NewsDomainError):
    """`audience_type`/`group_ids` combination is not valid."""


def validate_status_transition(current: str, target: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise InvalidNewsStatusTransitionError(current, target)


def ensure_editable(status: str) -> None:
    if status == ARCHIVED:
        raise NewsArchivedError()


def normalize_title(value: str) -> str:
    title = value.strip()
    if not title:
        raise InvalidNewsContentError("title must not be empty")
    if len(title) > TITLE_MAX_LENGTH:
        raise InvalidNewsContentError(f"title must be at most {TITLE_MAX_LENGTH} characters")
    return title


def normalize_body(value: str) -> str:
    if not value.strip():
        raise InvalidNewsContentError("body must not be empty")
    return value


def normalize_location(value: str | None) -> str | None:
    if value is None:
        return None
    location = value.strip()
    if not location:
        return None
    if len(location) > LOCATION_MAX_LENGTH:
        raise InvalidNewsContentError(f"location must be at most {LOCATION_MAX_LENGTH} characters")
    return location


def validate_audience(audience_type: str, group_count: int) -> None:
    """news.md §6: selected groups are required when the audience type is
    `groups`; an entire-club News has no selected groups."""
    if audience_type not in CANONICAL_NEWS_AUDIENCE_TYPES:
        raise InvalidNewsAudienceError(f"Unsupported audience_type: {audience_type!r}")
    if audience_type == AUDIENCE_GROUPS and group_count == 0:
        raise InvalidNewsAudienceError("group_ids is required when audience_type is 'groups'")
    if audience_type == AUDIENCE_CLUB and group_count > 0:
        raise InvalidNewsAudienceError("group_ids must be empty when audience_type is 'club'")


__all__ = [
    "TITLE_MAX_LENGTH",
    "LOCATION_MAX_LENGTH",
    "NewsDomainError",
    "InvalidNewsStatusTransitionError",
    "NewsArchivedError",
    "InvalidNewsContentError",
    "InvalidNewsAudienceError",
    "validate_status_transition",
    "ensure_editable",
    "normalize_title",
    "normalize_body",
    "normalize_location",
    "validate_audience",
]
