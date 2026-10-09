"""`/api/v1/me/telegram-link` — the authenticated User's own Telegram
link (Issue #329, ADR-0047 §4).

Self-scoped like `/auth/sessions`: the only identity input is the
session-derived `CurrentPrincipal`, so no RBAC permission is involved and
no request field can name another User. No endpoint accepts a Telegram id:
the Telegram identity is established only by the bot receiving
`/start <token>` from that account (app.telegram.updates).

- `GET` — whether the caller has an active Telegram identity.
- `POST /challenges` — issue (or reissue, revoking the previous pending
  one) a one-time challenge; returns the deep link to the configured bot.
  Rate-limited per User (429). 503 `telegram_linking_unavailable` when
  TELEGRAM_BOT_USERNAME is not configured. `Cache-Control: no-store`: the
  deep link carries the raw token.
- `DELETE` — unlink the active identity and revoke pending challenges.
  Idempotent 204.
"""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.request_context import get_request_id
from app.api.v1.telegram_link_schemas import TelegramLinkChallengeOut, TelegramLinkStatusOut
from app.core.config import ConfigurationError, get_telegram_bot_username
from app.db.session import get_db
from app.telegram import linking

router = APIRouter(prefix="/me/telegram-link", tags=["me"])

_NO_STORE = {"Cache-Control": "no-store"}


@router.get("", response_model=TelegramLinkStatusOut)
def get_my_telegram_link(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> TelegramLinkStatusOut:
    link = linking.get_link_status(db, user_id=principal.user_id)
    return TelegramLinkStatusOut(linked=link.linked, linked_at=link.linked_at)


@router.post(
    "/challenges",
    response_model=TelegramLinkChallengeOut,
    status_code=status.HTTP_201_CREATED,
)
def create_my_telegram_link_challenge(
    request: Request,
    response: Response,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> TelegramLinkChallengeOut:
    try:
        bot_username = get_telegram_bot_username()
    except ConfigurationError:
        bot_username = None
    if bot_username is None:
        raise APIError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "telegram_linking_unavailable",
            "Telegram linking is not available",
        )
    try:
        issued = linking.create_link_challenge(
            db,
            user_id=principal.user_id,
            bot_username=bot_username,
            request_id=get_request_id(request),
        )
        db.commit()
    except linking.LinkChallengeRateLimited as exc:
        db.rollback()
        raise APIError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too_many_requests",
            "Too many requests, please try again later",
        ) from exc
    except linking.LinkingUnavailable as exc:
        db.rollback()
        raise APIError(
            status.HTTP_403_FORBIDDEN, "forbidden", "Telegram linking is not allowed"
        ) from exc
    except Exception:
        db.rollback()
        raise
    response.headers.update(_NO_STORE)
    return TelegramLinkChallengeOut(deep_link=issued.deep_link, expires_at=issued.expires_at)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def unlink_my_telegram(
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> None:
    try:
        linking.unlink_telegram_identity(
            db, user_id=principal.user_id, request_id=get_request_id(request)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
