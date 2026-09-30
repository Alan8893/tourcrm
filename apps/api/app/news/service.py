"""News write operations (TH-0120 / Issue #227; docs/04-ux/news.md §5-§7).

No authorization here: the API router has already required the
Administrator (app.news.visibility.is_news_administrator) before any
function below runs, and locked the News row (`SELECT ... FOR UPDATE`)
for every mutation of an existing News.

Validation:

- content/audience/lifecycle rules: app.news.lifecycle;
- selected groups must exist and belong to the News' Club (the same
  not-found / Club-mismatch pair app.events.service applies to
  EventGroupTarget, ADR-0022);
- a linked Event must exist and belong to the News' Club.

There is no physical deletion of News anywhere: "delete" is `archive`.
Selected-group rows are targeting configuration and are replaced when
the audience changes.
"""

import uuid
from collections.abc import Sequence
from datetime import date, datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.events import Event
from app.db.groups import Group
from app.db.news import News, NewsGroupTarget
from app.news.lifecycle import (
    ensure_editable,
    normalize_body,
    normalize_location,
    normalize_title,
    validate_audience,
    validate_status_transition,
)
from app.news.vocabulary import (
    ARCHIVED,
    AUDIENCE_CLUB,
    CREATABLE_NEWS_STATUSES,
    PUBLISHED,
)


class NewsReferenceError(Exception):
    """Base class for invalid Group/Event references."""


class NewsGroupNotFoundError(NewsReferenceError):
    def __init__(self, group_id: uuid.UUID) -> None:
        super().__init__(f"Group {group_id} does not exist")
        self.group_id = group_id


class NewsGroupClubMismatchError(NewsReferenceError):
    def __init__(self, group_id: uuid.UUID) -> None:
        super().__init__(f"Group {group_id} does not belong to the News club")
        self.group_id = group_id


class NewsEventNotFoundError(NewsReferenceError):
    def __init__(self, event_id: uuid.UUID) -> None:
        super().__init__(f"Event {event_id} does not exist in the News club")
        self.event_id = event_id


class InvalidInitialNewsStatusError(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(f"News cannot be created with status {status!r}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dedupe(group_ids: Sequence[uuid.UUID] | None) -> list[uuid.UUID]:
    return list(dict.fromkeys(group_ids or []))


def _validate_groups(session: Session, *, club_id: uuid.UUID, group_ids: list[uuid.UUID]) -> None:
    if not group_ids:
        return
    rows: dict[uuid.UUID, uuid.UUID] = {
        group_id: group_club_id
        for group_id, group_club_id in session.execute(
            sa.select(Group.id, Group.club_id).where(Group.id.in_(group_ids))
        ).all()
    }
    for group_id in group_ids:
        if group_id not in rows:
            raise NewsGroupNotFoundError(group_id)
        if rows[group_id] != club_id:
            raise NewsGroupClubMismatchError(group_id)


def _validate_event(session: Session, *, club_id: uuid.UUID, event_id: uuid.UUID | None) -> None:
    if event_id is None:
        return
    event_club_id = session.execute(
        sa.select(Event.club_id).where(Event.id == event_id)
    ).scalar_one_or_none()
    if event_club_id is None or event_club_id != club_id:
        raise NewsEventNotFoundError(event_id)


def get_group_ids(session: Session, news_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        session.execute(
            sa.select(NewsGroupTarget.group_id)
            .where(NewsGroupTarget.news_id == news_id)
            .order_by(NewsGroupTarget.created_at, NewsGroupTarget.group_id)
        ).scalars()
    )


def _replace_group_targets(session: Session, *, news_id: uuid.UUID, group_ids: list[uuid.UUID]):
    session.execute(sa.delete(NewsGroupTarget).where(NewsGroupTarget.news_id == news_id))
    for group_id in group_ids:
        session.add(NewsGroupTarget(news_id=news_id, group_id=group_id))


def create_news(
    session: Session,
    *,
    club_id: uuid.UUID,
    title: str,
    body: str,
    status: str,
    audience_type: str,
    group_ids: Sequence[uuid.UUID] | None,
    event_date: date | None,
    location: str | None,
    event_id: uuid.UUID | None,
    created_by: uuid.UUID,
) -> News:
    if status not in CREATABLE_NEWS_STATUSES:
        raise InvalidInitialNewsStatusError(status)
    groups = _dedupe(group_ids)
    validate_audience(audience_type, len(groups))
    normalized_title = normalize_title(title)
    normalized_body = normalize_body(body)
    normalized_location = normalize_location(location)
    _validate_groups(session, club_id=club_id, group_ids=groups)
    _validate_event(session, club_id=club_id, event_id=event_id)

    news = News(
        id=uuid.uuid4(),
        club_id=club_id,
        title=normalized_title,
        body=normalized_body,
        status=status,
        audience_type=audience_type,
        published_at=_now() if status == PUBLISHED else None,
        event_date=event_date,
        location=normalized_location,
        event_id=event_id,
        created_by=created_by,
    )
    session.add(news)
    session.flush()
    _replace_group_targets(session, news_id=news.id, group_ids=groups)
    session.commit()
    session.refresh(news)
    return news


UPDATABLE_NEWS_FIELDS = frozenset(
    {"title", "body", "audience_type", "group_ids", "event_date", "location", "event_id"}
)


def update_news(
    session: Session, *, news: News, updated_by: uuid.UUID, fields: dict[str, Any]
) -> News:
    """Partial update of content/audience/Event link. `fields` holds only
    keys the client actually sent (PATCH semantics)."""
    unknown = set(fields) - UPDATABLE_NEWS_FIELDS
    if unknown:  # pragma: no cover - the request schema forbids these
        raise ValueError(f"Not updatable: {sorted(unknown)}")
    ensure_editable(news.status)

    audience_type = fields.get("audience_type", news.audience_type)
    if "group_ids" in fields:
        groups = _dedupe(fields["group_ids"])
    elif "audience_type" in fields and audience_type == AUDIENCE_CLUB:
        groups = []
    else:
        groups = get_group_ids(session, news.id)
    validate_audience(audience_type, len(groups))

    audience_changed = "group_ids" in fields or "audience_type" in fields
    # Validate everything before mutating anything.
    title = normalize_title(fields["title"]) if "title" in fields else news.title
    body = normalize_body(fields["body"]) if "body" in fields else news.body
    location = normalize_location(fields["location"]) if "location" in fields else news.location
    if "event_id" in fields:
        _validate_event(session, club_id=news.club_id, event_id=fields["event_id"])
    if audience_changed:
        _validate_groups(session, club_id=news.club_id, group_ids=groups)

    news.title = title
    news.body = body
    news.location = location
    if "event_date" in fields:
        news.event_date = fields["event_date"]
    if "event_id" in fields:
        news.event_id = fields["event_id"]
    if audience_changed:
        news.audience_type = audience_type
        _replace_group_targets(session, news_id=news.id, group_ids=groups)

    news.updated_by = updated_by
    session.commit()
    session.refresh(news)
    return news


def publish_news(session: Session, *, news: News, updated_by: uuid.UUID) -> News:
    validate_status_transition(news.status, PUBLISHED)
    news.status = PUBLISHED
    news.published_at = _now()
    news.updated_by = updated_by
    session.commit()
    session.refresh(news)
    return news


def archive_news(session: Session, *, news: News, updated_by: uuid.UUID) -> News:
    """Soft delete: the row, its image and its targeting are preserved."""
    validate_status_transition(news.status, ARCHIVED)
    news.status = ARCHIVED
    news.archived_at = _now()
    news.updated_by = updated_by
    session.commit()
    session.refresh(news)
    return news


__all__ = [
    "NewsReferenceError",
    "NewsGroupNotFoundError",
    "NewsGroupClubMismatchError",
    "NewsEventNotFoundError",
    "InvalidInitialNewsStatusError",
    "UPDATABLE_NEWS_FIELDS",
    "get_group_ids",
    "create_news",
    "update_news",
    "publish_news",
    "archive_news",
]
