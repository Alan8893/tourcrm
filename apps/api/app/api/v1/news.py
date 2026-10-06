"""News / Announcements API — /api/v1/news (TH-0120 / Issue #227).

Canonical sources: docs/04-ux/news.md, Issue #227's contract; API
conventions from docs/05-api/api-conventions.md / ADR-0014 (envelopes)
and the existing Event/Profile-photo routers (action endpoints, image
sub-resource, CSRF on every state change).

    GET    /news                    list News visible to the caller
    GET    /news/{news_id}          detail (404 when not visible)
    POST   /news                    create            (Administrator)
    PATCH  /news/{news_id}          update            (Administrator)
    POST   /news/{news_id}/publish  draft -> published (Administrator)
    POST   /news/{news_id}/archive  -> archived, soft delete (Administrator)
    GET    /news/{news_id}/image    current image (same visibility as detail)
    PUT    /news/{news_id}/image    create/replace image (Administrator)
    DELETE /news/{news_id}/image    remove image      (Administrator)

There is deliberately no `DELETE /news/{news_id}`: deletion is the archive
action (news.md §5), and the News record is never physically removed.

Authorization (news.md §7/§9): every mutation requires the Administrator
(app.news.visibility.is_news_administrator), checked *before* the target
News is even loaded — so a non-Administrator gets the same 403 whether or
not the id exists (no existence probing). Reads apply
app.news.visibility.news_visibility_filter inside the SQL query: the list
silently excludes invisible rows, and a detail/image read of a News the
caller may not see (other group's, draft, archived) is answered exactly
like a nonexistent one — 404 (existence-hiding, as for Events).
"""

import logging
import uuid
from typing import Literal, NoReturn

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.news_schemas import (
    NewsCreateRequest,
    NewsLinkedEventOut,
    NewsOut,
    NewsUpdateRequest,
)
from app.authorization.service import AuthorizationDenied, Authorizer
from app.db.events import Event
from app.db.news import News
from app.db.session import get_db
from app.events.authorization import build_event_resource_context
from app.imports.authorization import resolve_sole_club_id
from app.news import service as news_service
from app.news.image import NEWS_IMAGE_MIME_TYPE, delete_news_image, read_news_image, set_news_image
from app.news.lifecycle import (
    InvalidNewsAudienceError,
    InvalidNewsContentError,
    InvalidNewsStatusTransitionError,
    NewsArchivedError,
    NewsDomainError,
    ensure_editable,
)
from app.news.visibility import is_news_administrator, news_visibility_filter
from app.people.photo_image import MAX_PHOTO_UPLOAD_BYTES, InvalidPhotoError, PhotoTooLargeError
from app.storage.file_storage import FileStorage
from app.storage.local import get_file_storage

logger = logging.getLogger("tourcrm.api.news")

router = APIRouter(prefix="/news", tags=["news"])

# Never shown to the client: the 403 envelope carries no permission detail.
ADMINISTRATOR_REQUIREMENT = "news.administrator"

_NON_NULLABLE_UPDATE_FIELDS = ("title", "body", "audience_type", "group_ids")


def _not_found() -> APIError:
    return APIError(status.HTTP_404_NOT_FOUND, "not_found", "News not found")


def _require_administrator(db: Session, user_id: uuid.UUID) -> uuid.UUID:
    club_id = resolve_sole_club_id(db)
    if not is_news_administrator(db, user_id=user_id, club_id=club_id):
        raise AuthorizationDenied(ADMINISTRATOR_REQUIREMENT)
    return club_id


def _get_visible_news_or_404(db: Session, *, news_id: uuid.UUID, user_id: uuid.UUID) -> News:
    club_id = resolve_sole_club_id(db)
    stmt = sa.select(News).where(
        News.id == news_id, news_visibility_filter(db, user_id=user_id, club_id=club_id)
    )
    news = db.execute(stmt).scalar_one_or_none()
    if news is None:
        raise _not_found()
    return news


def _get_managed_news_or_404(db: Session, *, news_id: uuid.UUID, club_id: uuid.UUID) -> News:
    news = db.execute(
        sa.select(News).where(News.id == news_id, News.club_id == club_id).with_for_update()
    ).scalar_one_or_none()
    if news is None:
        raise _not_found()
    return news


def _linked_event_out(
    db: Session, *, news: News, user_id: uuid.UUID, is_admin: bool
) -> NewsLinkedEventOut | None:
    if news.event_id is None:
        return None
    event = db.get(Event, news.event_id)
    if event is None:
        return None
    if not is_admin:
        context = build_event_resource_context(
            db, event=event, user_id=user_id, permission_code="event.read"
        )
        if not Authorizer(session=db, user_id=user_id, permission_code="event.read").is_allowed(
            context
        ):
            return None
    return NewsLinkedEventOut(
        id=event.id,
        title=event.title,
        start_at=event.start_at,
        end_at=event.end_at,
        status=event.status,
    )


def _news_out(db: Session, news: News, *, user_id: uuid.UUID, is_admin: bool) -> NewsOut:
    return NewsOut(
        id=news.id,
        title=news.title,
        body=news.body,
        status=news.status,  # type: ignore[arg-type]
        published_at=news.published_at,
        archived_at=news.archived_at,
        event_date=news.event_date,
        location=news.location,
        audience_type=news.audience_type,  # type: ignore[arg-type]
        group_ids=news_service.get_group_ids(db, news.id) if is_admin else None,
        image_file_id=news.image_file_id,
        linked_event=_linked_event_out(db, news=news, user_id=user_id, is_admin=is_admin),
        created_by=news.created_by,
        updated_by=news.updated_by,
        created_at=news.created_at,
        updated_at=news.updated_at,
    )


def _raise_for_domain_error(exc: Exception) -> NoReturn:
    if isinstance(exc, InvalidNewsStatusTransitionError):
        raise APIError(status.HTTP_409_CONFLICT, "invalid_status_transition", str(exc)) from exc
    if isinstance(exc, NewsArchivedError):
        raise APIError(status.HTTP_409_CONFLICT, "news_archived", str(exc)) from exc
    if isinstance(exc, InvalidNewsAudienceError):
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_audience", str(exc)) from exc
    if isinstance(exc, news_service.NewsGroupNotFoundError):
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_group_id", str(exc)) from exc
    if isinstance(exc, news_service.NewsGroupClubMismatchError):
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "group_club_mismatch", str(exc)
        ) from exc
    if isinstance(exc, news_service.NewsEventNotFoundError):
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_event_id", str(exc)) from exc
    if isinstance(exc, (InvalidNewsContentError, news_service.InvalidInitialNewsStatusError)):
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_news_data", str(exc)) from exc
    raise exc


@router.get("", response_model=CollectionResponse[NewsOut])
def list_news(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_: Literal["published", "draft", "archived", "all"] = Query(
        default="published", alias="status"
    ),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[NewsOut]:
    """Newest first. `status` defaults to `published` (Home / ordinary
    list, for every role including Administrator); `draft`/`archived`/
    `all` are the management views — for a non-Administrator the
    visibility filter already restricts rows to `published`, so those
    values can never reveal anything."""
    club_id = resolve_sole_club_id(db)
    is_admin = is_news_administrator(db, user_id=principal.user_id, club_id=club_id)
    conditions = [news_visibility_filter(db, user_id=principal.user_id, club_id=club_id)]
    if status_ != "all":
        conditions.append(News.status == status_)

    total = db.execute(sa.select(sa.func.count()).select_from(News).where(*conditions)).scalar_one()
    rows = (
        db.execute(
            sa.select(News)
            .where(*conditions)
            .order_by(
                News.published_at.desc().nulls_first(), News.created_at.desc(), News.id.desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_news_out(db, row, user_id=principal.user_id, is_admin=is_admin) for row in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.get("/{news_id}", response_model=NewsOut)
def get_news(
    news_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> NewsOut:
    news = _get_visible_news_or_404(db, news_id=news_id, user_id=principal.user_id)
    is_admin = is_news_administrator(db, user_id=principal.user_id, club_id=news.club_id)
    return _news_out(db, news, user_id=principal.user_id, is_admin=is_admin)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=NewsOut)
def create_news(
    payload: NewsCreateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> NewsOut:
    club_id = _require_administrator(db, principal.user_id)
    try:
        news = news_service.create_news(
            db,
            club_id=club_id,
            title=payload.title,
            body=payload.body,
            status=payload.status,
            audience_type=payload.audience_type,
            group_ids=payload.group_ids,
            event_date=payload.event_date,
            location=payload.location,
            event_id=payload.event_id,
            created_by=principal.user_id,
        )
    except (
        NewsDomainError,
        news_service.NewsReferenceError,
        news_service.InvalidInitialNewsStatusError,
    ) as exc:
        _raise_for_domain_error(exc)
    logger.info("news.create.success news_id=%s user_id=%s", news.id, principal.user_id)
    return _news_out(db, news, user_id=principal.user_id, is_admin=True)


@router.patch("/{news_id}", response_model=NewsOut)
def update_news(
    news_id: uuid.UUID,
    payload: NewsUpdateRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> NewsOut:
    club_id = _require_administrator(db, principal.user_id)
    news = _get_managed_news_or_404(db, news_id=news_id, club_id=club_id)
    fields = payload.model_dump(exclude_unset=True)
    for field_name in _NON_NULLABLE_UPDATE_FIELDS:
        if field_name in fields and fields[field_name] is None:
            raise APIError(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_news_data",
                f"{field_name} cannot be null",
            )
    try:
        news = news_service.update_news(db, news=news, updated_by=principal.user_id, fields=fields)
    except (NewsDomainError, news_service.NewsReferenceError) as exc:
        _raise_for_domain_error(exc)
    logger.info("news.update.success news_id=%s user_id=%s", news.id, principal.user_id)
    return _news_out(db, news, user_id=principal.user_id, is_admin=True)


@router.post("/{news_id}/publish", response_model=NewsOut)
def publish_news(
    news_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> NewsOut:
    club_id = _require_administrator(db, principal.user_id)
    news = _get_managed_news_or_404(db, news_id=news_id, club_id=club_id)
    try:
        news = news_service.publish_news(db, news=news, updated_by=principal.user_id)
    except NewsDomainError as exc:
        _raise_for_domain_error(exc)
    logger.info("news.publish.success news_id=%s user_id=%s", news.id, principal.user_id)
    return _news_out(db, news, user_id=principal.user_id, is_admin=True)


@router.post("/{news_id}/archive", response_model=NewsOut)
def archive_news(
    news_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> NewsOut:
    club_id = _require_administrator(db, principal.user_id)
    news = _get_managed_news_or_404(db, news_id=news_id, club_id=club_id)
    try:
        news = news_service.archive_news(db, news=news, updated_by=principal.user_id)
    except NewsDomainError as exc:
        _raise_for_domain_error(exc)
    logger.info("news.archive.success news_id=%s user_id=%s", news.id, principal.user_id)
    return _news_out(db, news, user_id=principal.user_id, is_admin=True)


# --- image sub-resource (same mechanics as /persons/{id}/photo) -------------


@router.get("/{news_id}/image")
def get_news_image(
    news_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
) -> Response:
    news = _get_visible_news_or_404(db, news_id=news_id, user_id=principal.user_id)
    current = read_news_image(db, storage, news=news)
    if current is None:
        raise APIError(status.HTTP_404_NOT_FOUND, "image_not_found", "Image not found")
    file_id, content = current
    etag = f'"{file_id}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=content, media_type=NEWS_IMAGE_MIME_TYPE, headers=headers)


@router.put("/{news_id}/image", response_model=NewsOut)
def put_news_image(
    news_id: uuid.UUID,
    image: UploadFile = File(...),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
    _csrf: None = Depends(require_csrf_token),
) -> NewsOut:
    club_id = _require_administrator(db, principal.user_id)
    news = _get_managed_news_or_404(db, news_id=news_id, club_id=club_id)
    try:
        ensure_editable(news.status)
    except NewsDomainError as exc:
        _raise_for_domain_error(exc)
    content = image.file.read(MAX_PHOTO_UPLOAD_BYTES + 1)
    try:
        news = set_news_image(
            db, storage, news=news, content=content, actor_user_id=principal.user_id
        )
    except PhotoTooLargeError as exc:
        raise APIError(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "image_too_large",
            "Image exceeds the 10 MB upload limit",
        ) from exc
    except InvalidPhotoError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_image",
            "Image must be a valid JPEG, PNG or WebP image",
        ) from exc
    return _news_out(db, news, user_id=principal.user_id, is_admin=True)


@router.delete("/{news_id}/image", response_model=NewsOut)
def delete_news_image_endpoint(
    news_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_file_storage),
    _csrf: None = Depends(require_csrf_token),
) -> NewsOut:
    club_id = _require_administrator(db, principal.user_id)
    news = _get_managed_news_or_404(db, news_id=news_id, club_id=club_id)
    try:
        ensure_editable(news.status)
    except NewsDomainError as exc:
        _raise_for_domain_error(exc)
    news = delete_news_image(db, storage, news=news, actor_user_id=principal.user_id)
    return _news_out(db, news, user_id=principal.user_id, is_admin=True)


__all__ = ["router"]
