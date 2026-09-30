"""Canonical News vocabularies (docs/04-ux/news.md §2/§5).

Pure Python — no ORM/FastAPI import, mirroring app.events.vocabulary:
app.db.news builds its CHECK constraints from these sets and
app.news.lifecycle validates transitions against them.
"""

DRAFT = "draft"
PUBLISHED = "published"
ARCHIVED = "archived"

# news.md §5: the closed lifecycle vocabulary.
CANONICAL_NEWS_STATUSES: frozenset[str] = frozenset({DRAFT, PUBLISHED, ARCHIVED})

# The statuses an Administrator may choose when creating News (the
# management form's "publication status"); `archived` is only ever reached
# through the archive action.
CREATABLE_NEWS_STATUSES: frozenset[str] = frozenset({DRAFT, PUBLISHED})

AUDIENCE_CLUB = "club"
AUDIENCE_GROUPS = "groups"

# news.md §2: "Entire club" or "Selected groups" — role-only targeting is
# out of scope.
CANONICAL_NEWS_AUDIENCE_TYPES: frozenset[str] = frozenset({AUDIENCE_CLUB, AUDIENCE_GROUPS})

__all__ = [
    "DRAFT",
    "PUBLISHED",
    "ARCHIVED",
    "CANONICAL_NEWS_STATUSES",
    "CREATABLE_NEWS_STATUSES",
    "AUDIENCE_CLUB",
    "AUDIENCE_GROUPS",
    "CANONICAL_NEWS_AUDIENCE_TYPES",
]
