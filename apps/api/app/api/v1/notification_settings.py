"""`/api/v1/settings/notifications` — Administrator Notification Settings
(Issue #333, ADR-0048; contract: docs/05-api/notification-settings-api.md).

Authorization is enforced here, never by the UI (ADR-0048 §2.5/§2.7):

- `notification.manage` — Global Admin Policy, installation-wide rules,
  channel status, Telegram destination options and test send;
- `settings.manage` — SMTP/Telegram integration settings and their secrets.

Both are checked against the installation's single Club (the same pattern
as other installation-wide administration, e.g. tourism types); without
exactly one Club the check fails closed. Every state-changing endpoint
needs the CSRF header.

Secrets are write-only: `PUT .../password` / `PUT .../bot-token` set or
replace a value, `DELETE` clears it; no endpoint ever returns a value, a
ciphertext or a key, and saving the non-secret settings never touches a
secret. A secret write without a usable encryption key ring answers 503
`settings_encryption_unavailable` and stores nothing.
"""

import re
import uuid
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, require_authenticated_principal, require_csrf_token
from app.api.errors import APIError
from app.api.request_context import get_request_id
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.notification_settings_schemas import (
    ChannelStatusOut,
    EmailSettingsIn,
    EmailSettingsOut,
    IntegrationsOut,
    NotificationStatusOut,
    PolicyIn,
    PolicyOut,
    RuleOut,
    RuleUpdateIn,
    SecretIn,
    SecretStatusOut,
    TelegramDestinationOptionOut,
    TelegramSettingsIn,
    TelegramSettingsOut,
    TelegramStatusOut,
    TestSendIn,
    TestSendOut,
)
from app.authorization.context import ResourceContext
from app.authorization.service import AuthorizationDenied, can
from app.core.config import (
    TELEGRAM_BOT_TOKEN_PATTERN,
    ConfigurationError,
    validate_telegram_bot_username,
)
from app.db.notifications import NotificationRule, TelegramDestination
from app.db.session import get_db
from app.imports.authorization import (
    MultipleClubsConfiguredError,
    NoClubConfiguredError,
    resolve_sole_club_id,
)
from app.notification_settings import service
from app.notification_settings.runtime import (
    encryption_status,
    global_channel_enabled,
    load_email_runtime,
    load_telegram_runtime,
    secret_configured,
)
from app.notification_settings.test_send import (
    TestSendRateLimited,
    TestSendRequest,
    send_test_message,
)
from app.notification_settings.vocabulary import (
    CONFIG_CONFIGURED,
    SECRET_SMTP_PASSWORD,
    SECRET_TELEGRAM_BOT_TOKEN,
)
from app.notifications.email_adapter import parse_email_address
from app.notifications.vocabulary import CHANNEL_EMAIL, CHANNEL_TELEGRAM

router = APIRouter(prefix="/settings/notifications", tags=["settings"])

NOTIFICATION_MANAGE = "notification.manage"
SETTINGS_MANAGE = "settings.manage"

# A host name, IPv4 address or bracketed IPv6 address; no spaces, no
# userinfo, no scheme, no line breaks.
_SMTP_HOST_PATTERN = re.compile(r"[A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\]")
_NO_STORE = {"Cache-Control": "no-store"}


def _authorize(db: Session, user_id: uuid.UUID, permission_code: str) -> None:
    try:
        club_id = resolve_sole_club_id(db)
    except (NoClubConfiguredError, MultipleClubsConfiguredError):
        raise AuthorizationDenied(permission_code) from None
    if not can(db, user_id, permission_code, ResourceContext(club_id=club_id)):
        raise AuthorizationDenied(permission_code)


def _validation_error(code: str, message: str) -> APIError:
    return APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, code, message)


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _no_line_breaks(value: Optional[str], code: str, message: str) -> Optional[str]:
    if value is not None and any(ch in value for ch in "\r\n\0"):
        raise _validation_error(code, message)
    return value


# --- Global Admin Policy ------------------------------------------------------------------


@router.get("/policy", response_model=PolicyOut)
def get_policy(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> PolicyOut:
    _authorize(db, principal.user_id, NOTIFICATION_MANAGE)
    view = service.get_policy(db)
    return PolicyOut(
        email_enabled=view.email_enabled, telegram_enabled=view.telegram_enabled, saved=view.saved
    )


@router.put("/policy", response_model=PolicyOut)
def update_policy(
    payload: PolicyIn,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> PolicyOut:
    _authorize(db, principal.user_id, NOTIFICATION_MANAGE)
    try:
        view = service.update_policy(
            db,
            actor_user_id=principal.user_id,
            email_enabled=payload.email_enabled,
            telegram_enabled=payload.telegram_enabled,
            request_id=get_request_id(request),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return PolicyOut(
        email_enabled=view.email_enabled, telegram_enabled=view.telegram_enabled, saved=True
    )


# --- Installation-wide rules --------------------------------------------------------------


def _rule_out(rule: NotificationRule) -> RuleOut:
    return RuleOut(
        id=rule.id,
        event_type=rule.event_type,
        channel=rule.channel,
        recipient_scope=rule.recipient_scope,
        is_enabled=rule.is_enabled,
    )


@router.get("/rules", response_model=CollectionResponse[RuleOut])
def list_rules(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[RuleOut]:
    _authorize(db, principal.user_id, NOTIFICATION_MANAGE)
    rules = service.list_installation_rules(db)
    total = len(rules)
    return CollectionResponse(
        items=[_rule_out(rule) for rule in rules],
        pagination=Pagination(page=1, page_size=total or 1, total=total, pages=1 if total else 0),
    )


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: uuid.UUID,
    payload: RuleUpdateIn,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> RuleOut:
    _authorize(db, principal.user_id, NOTIFICATION_MANAGE)
    try:
        rule = service.set_rule_enabled(
            db,
            actor_user_id=principal.user_id,
            rule_id=rule_id,
            is_enabled=payload.is_enabled,
            request_id=get_request_id(request),
        )
        db.commit()
    except service.RuleNotFound as exc:
        db.rollback()
        raise APIError(
            status.HTTP_404_NOT_FOUND, "rule_not_found", "Notification rule not found"
        ) from exc
    except Exception:
        db.rollback()
        raise
    return _rule_out(rule)


# --- Status and test send -----------------------------------------------------------------


@router.get("/status", response_model=NotificationStatusOut)
def get_status(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> NotificationStatusOut:
    _authorize(db, principal.user_id, NOTIFICATION_MANAGE)
    email_enabled = global_channel_enabled(db, CHANNEL_EMAIL)
    telegram_enabled = global_channel_enabled(db, CHANNEL_TELEGRAM)
    email = load_email_runtime(db)
    telegram = load_telegram_runtime(db)
    return NotificationStatusOut(
        encryption=encryption_status(),  # type: ignore[arg-type]
        email=ChannelStatusOut(
            policy_enabled=email_enabled,
            configuration=email.state,  # type: ignore[arg-type]
            ready=email_enabled and email.state == CONFIG_CONFIGURED,
        ),
        telegram=TelegramStatusOut(
            policy_enabled=telegram_enabled,
            configuration=telegram.state,  # type: ignore[arg-type]
            ready=telegram_enabled and telegram.state == CONFIG_CONFIGURED,
            linking_available=telegram.bot_username is not None,
        ),
    )


@router.get(
    "/telegram-destinations", response_model=CollectionResponse[TelegramDestinationOptionOut]
)
def list_test_telegram_destinations(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[TelegramDestinationOptionOut]:
    """Enabled existing destinations a test message may be sent to."""
    _authorize(db, principal.user_id, NOTIFICATION_MANAGE)
    rows = db.execute(
        sa.select(TelegramDestination)
        .where(TelegramDestination.enabled.is_(True))
        .order_by(TelegramDestination.name, TelegramDestination.topic_name)
    ).scalars().all()
    total = len(rows)
    return CollectionResponse(
        items=[
            TelegramDestinationOptionOut(id=row.id, name=row.name, topic_name=row.topic_name)
            for row in rows
        ],
        pagination=Pagination(page=1, page_size=total or 1, total=total, pages=1 if total else 0),
    )


@router.post("/test-send", response_model=TestSendOut)
def test_send(
    payload: TestSendIn,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> TestSendOut:
    _authorize(db, principal.user_id, NOTIFICATION_MANAGE)
    valid_kinds = {
        CHANNEL_EMAIL: {"email_address"},
        CHANNEL_TELEGRAM: {"own_telegram_account", "telegram_destination"},
    }
    if payload.destination_kind not in valid_kinds[payload.channel]:
        raise _validation_error(
            "invalid_test_destination", "The destination does not match the channel"
        )
    if payload.destination_kind == "email_address" and parse_email_address(payload.email) is None:
        raise _validation_error("invalid_email", "Enter a valid email address")
    if (
        payload.destination_kind == "telegram_destination"
        and payload.telegram_destination_id is None
    ):
        raise _validation_error("invalid_test_destination", "Choose a Telegram destination")
    # The authorization reads above must not keep a transaction open into
    # the provider call made by send_test_message.
    db.rollback()
    try:
        result = send_test_message(
            db,
            actor_user_id=principal.user_id,
            request=TestSendRequest(
                channel=payload.channel,
                destination_kind=payload.destination_kind,
                email=payload.email,
                telegram_destination_id=payload.telegram_destination_id,
            ),
            request_id=get_request_id(request),
        )
    except TestSendRateLimited as exc:
        raise APIError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too_many_requests",
            "Too many test messages, please try again later",
        ) from exc
    except Exception:
        db.rollback()
        raise
    return TestSendOut(
        channel=result.channel,  # type: ignore[arg-type]
        destination_kind=result.destination_kind,
        status=result.status,  # type: ignore[arg-type]
        error_code=result.error_code,
    )


# --- Integrations (settings.manage) -------------------------------------------------------


def _email_out(db: Session) -> EmailSettingsOut:
    row = service.get_email_settings(db)
    return EmailSettingsOut(
        smtp_host=row.smtp_host if row else None,
        smtp_port=row.smtp_port if row else None,
        smtp_security=row.smtp_security if row else "starttls",  # type: ignore[arg-type]
        smtp_username=row.smtp_username if row else None,
        sender_email=row.sender_email if row else None,
        sender_name=row.sender_name if row else None,
        password_configured=secret_configured(db, SECRET_SMTP_PASSWORD),
    )


def _telegram_out(db: Session) -> TelegramSettingsOut:
    row = service.get_telegram_settings(db)
    return TelegramSettingsOut(
        bot_username=row.bot_username if row else None,
        bot_token_configured=secret_configured(db, SECRET_TELEGRAM_BOT_TOKEN),
    )


@router.get("/integrations", response_model=IntegrationsOut)
def get_integrations(
    response: Response,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> IntegrationsOut:
    _authorize(db, principal.user_id, SETTINGS_MANAGE)
    response.headers.update(_NO_STORE)
    return IntegrationsOut(
        encryption=encryption_status(),  # type: ignore[arg-type]
        email=_email_out(db),
        telegram=_telegram_out(db),
    )


@router.put("/integrations/email", response_model=EmailSettingsOut)
def update_email_settings(
    payload: EmailSettingsIn,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> EmailSettingsOut:
    _authorize(db, principal.user_id, SETTINGS_MANAGE)
    host = _clean(payload.smtp_host)
    if host is not None and not _SMTP_HOST_PATTERN.fullmatch(host):
        raise _validation_error("invalid_smtp_host", "Enter a valid SMTP server host name")
    sender_email = _clean(payload.sender_email)
    if sender_email is not None:
        address = parse_email_address(sender_email)
        if address is None:
            raise _validation_error("invalid_sender_email", "Enter a valid sender email address")
        sender_email = address.addr_spec
    values = {
        "smtp_host": host,
        "smtp_port": payload.smtp_port,
        "smtp_security": payload.smtp_security,
        "smtp_username": _no_line_breaks(
            _clean(payload.smtp_username), "invalid_smtp_username", "Enter a valid SMTP username"
        ),
        "sender_email": sender_email,
        "sender_name": _no_line_breaks(
            _clean(payload.sender_name), "invalid_sender_name", "Enter a valid sender name"
        ),
    }
    try:
        service.update_email_settings(
            db, actor_user_id=principal.user_id, values=values, request_id=get_request_id(request)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _email_out(db)


@router.put("/integrations/telegram", response_model=TelegramSettingsOut)
def update_telegram_settings(
    payload: TelegramSettingsIn,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> TelegramSettingsOut:
    _authorize(db, principal.user_id, SETTINGS_MANAGE)
    username = _clean(payload.bot_username)
    if username is not None:
        try:
            username = validate_telegram_bot_username(username)
        except ConfigurationError:
            raise _validation_error(
                "invalid_bot_username", "Enter a valid Telegram bot username"
            ) from None
    try:
        service.update_telegram_settings(
            db,
            actor_user_id=principal.user_id,
            bot_username=username,
            request_id=get_request_id(request),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _telegram_out(db)


def _set_secret(
    db: Session, request: Request, principal: CurrentPrincipal, name: str, value: str
) -> SecretStatusOut:
    _authorize(db, principal.user_id, SETTINGS_MANAGE)
    try:
        service.set_secret(
            db,
            actor_user_id=principal.user_id,
            name=name,
            value=value,
            request_id=get_request_id(request),
        )
        db.commit()
    except service.EncryptionUnavailable as exc:
        db.rollback()
        raise APIError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "settings_encryption_unavailable",
            "Secrets cannot be saved: the settings encryption key is not configured correctly",
        ) from exc
    except Exception:
        db.rollback()
        raise
    return SecretStatusOut(configured=True)


def _clear_secret(db: Session, request: Request, principal: CurrentPrincipal, name: str) -> None:
    _authorize(db, principal.user_id, SETTINGS_MANAGE)
    try:
        service.clear_secret(
            db, actor_user_id=principal.user_id, name=name, request_id=get_request_id(request)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


@router.put("/integrations/email/password", response_model=SecretStatusOut)
def set_smtp_password(
    payload: SecretIn,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> SecretStatusOut:
    if not payload.value.strip() or any(ch in payload.value for ch in "\r\n\0"):
        raise _validation_error("invalid_secret", "Enter the new SMTP password")
    return _set_secret(db, request, principal, SECRET_SMTP_PASSWORD, payload.value)


@router.delete("/integrations/email/password", status_code=status.HTTP_204_NO_CONTENT)
def clear_smtp_password(
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> None:
    _clear_secret(db, request, principal, SECRET_SMTP_PASSWORD)


@router.put("/integrations/telegram/bot-token", response_model=SecretStatusOut)
def set_telegram_bot_token(
    payload: SecretIn,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> SecretStatusOut:
    token = payload.value.strip()
    if not TELEGRAM_BOT_TOKEN_PATTERN.fullmatch(token):
        raise _validation_error(
            "invalid_secret", "Enter the bot token exactly as issued by @BotFather"
        )
    return _set_secret(db, request, principal, SECRET_TELEGRAM_BOT_TOKEN, token)


@router.delete("/integrations/telegram/bot-token", status_code=status.HTTP_204_NO_CONTENT)
def clear_telegram_bot_token(
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> None:
    _clear_secret(db, request, principal, SECRET_TELEGRAM_BOT_TOKEN)
