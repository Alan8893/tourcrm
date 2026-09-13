"""Authentication API — /api/v1/auth (Issue #33).

Canonical source: docs/05-api/auth-api.md. Endpoints intentionally NOT
implemented here: invitation creation/acceptance (blocked by ODR-014 —
see docs/03-architecture/adr/ADR-0008-open-decisions.md), MFA/SSO/OAuth,
and any admin-approval-of-registration endpoint (auth-api.md never
defines one; that belongs to a future People/Membership admin API).
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    SESSION_COOKIE_NAME,
    CurrentPrincipal,
    get_current_principal,
    require_authenticated_principal,
    require_csrf_token,
)
from app.api.errors import APIError
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.auth_schemas import (
    ChangePasswordRequest,
    GenericResultResponse,
    LoginRequest,
    LoginResponse,
    MeResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequestRequest,
    PersonOut,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    RoleAssignmentOut,
    SessionOut,
    SessionSummaryOut,
    UserOut,
    VerifyEmailRequest,
)
from app.authentication import service as auth_service
from app.authentication.csrf import CSRF_COOKIE_NAME, generate_csrf_token
from app.authentication.passwords import WeakPasswordError
from app.authentication.rate_limit import RateLimiter, RateLimitExceeded, get_rate_limiter
from app.core.config import get_settings
from app.db.authorization import Role, UserRoleAssignment
from app.db.identity import Person, User
from app.db.session import get_db

logger = logging.getLogger("tourcrm.api")

router = APIRouter(prefix="/auth", tags=["auth"])


def _person_out(person: Person) -> PersonOut:
    return PersonOut(
        first_name=person.first_name,
        last_name=person.last_name,
        middle_name=person.middle_name,
        birth_date=person.birth_date,
    )


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        login_identifier=user.login_identifier,
        status=user.status,
        email_verified_at=user.email_verified_at,
        person=_person_out(user.person),
    )


def _apply_rate_limit(limiter: RateLimiter, key: str) -> None:
    try:
        limiter.check(key)
    except RateLimitExceeded as exc:
        raise APIError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too_many_requests",
            "Too many requests, please try again later",
        ) from exc


def _set_session_cookies(
    response: Response, *, raw_session_token: str, csrf_token: str, expires_at
) -> None:
    secure = get_settings().cookie_secure
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_session_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        expires=expires_at,
    )
    # Deliberately NOT HttpOnly: the double-submit CSRF pattern requires
    # same-origin JavaScript to be able to read this value and echo it
    # back in a header (app.authentication.csrf).
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
        expires=expires_at,
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> RegisterResponse:
    _apply_rate_limit(limiter, f"register:{payload.email.strip().lower()}")
    try:
        user, _raw_verification_token = auth_service.register(
            db,
            email=payload.email,
            password=payload.password,
            first_name=payload.first_name,
            last_name=payload.last_name,
            middle_name=payload.middle_name,
            birth_date=payload.birth_date,
        )
    except auth_service.EmailAlreadyRegisteredError as exc:
        raise APIError(
            status.HTTP_409_CONFLICT, "conflict", "An account with this identifier already exists"
        ) from exc
    except WeakPasswordError as exc:
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "weak_password", str(exc)) from exc
    logger.info("auth.register.success user_id=%s", user.id)
    return RegisterResponse(user=_user_out(user))


@router.post(
    "/verify-email", response_model=GenericResultResponse, response_model_exclude_none=False
)
def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    db: Session = Depends(get_db),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> GenericResultResponse:
    client_ip = request.client.host if request.client else "unknown"
    _apply_rate_limit(limiter, f"verify-email:{client_ip}")
    try:
        auth_service.verify_email(db, payload.token)
    except auth_service.InvalidOrExpiredTokenError as exc:
        # authentication-persistence.md §4: "repeated use has no
        # credential-disclosing side effect" — one deterministic error,
        # never distinguishing unknown/expired/already-used.
        raise APIError(
            status.HTTP_400_BAD_REQUEST, "invalid_or_expired_token", "Invalid or expired token"
        ) from exc
    logger.info("auth.verify_email.success")
    return GenericResultResponse()


@router.post("/resend-verification", response_model=GenericResultResponse)
def resend_verification(
    payload: ResendVerificationRequest,
    db: Session = Depends(get_db),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> GenericResultResponse:
    _apply_rate_limit(limiter, f"resend-verification:{payload.identifier.strip().lower()}")
    # auth-api.md §6: identical response whether or not the identifier
    # exists or is already verified — the return value is deliberately
    # discarded here.
    auth_service.resend_verification(db, payload.identifier)
    return GenericResultResponse()


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> LoginResponse:
    _apply_rate_limit(limiter, f"login:{payload.identifier.strip().lower()}")
    _apply_rate_limit(limiter, f"login:{request.client.host if request.client else 'unknown'}")
    try:
        user, raw_session_token, expires_at = auth_service.login(
            db,
            identifier=payload.identifier,
            password=payload.password,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    except auth_service.InvalidCredentialsError as exc:
        logger.info("auth.login.failure reason=invalid_credentials")
        raise APIError(
            status.HTTP_401_UNAUTHORIZED, "invalid_credentials", "Invalid credentials"
        ) from exc
    except auth_service.AccountNotActiveError as exc:
        logger.info("auth.login.failure reason=account_not_active status=%s", exc.status)
        raise APIError(
            status.HTTP_403_FORBIDDEN,
            f"account_{exc.status}",
            "This account cannot currently sign in",
        ) from exc

    csrf_token = generate_csrf_token()
    _set_session_cookies(
        response, raw_session_token=raw_session_token, csrf_token=csrf_token, expires_at=expires_at
    )
    logger.info("auth.login.success user_id=%s", user.id)
    return LoginResponse(
        user=_user_out(user),
        session=SessionSummaryOut(expires_at=expires_at),
        csrf_token=csrf_token,
    )


@router.post("/logout", response_model=GenericResultResponse)
def logout(
    request: Request,
    response: Response,
    # Deliberately the raw (nullable) dependency, not
    # require_authenticated_principal: logout must be idempotent even
    # when already logged out (auth-api.md §8), so an unauthenticated call
    # is a no-op success, not a 401.
    principal: CurrentPrincipal | None = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> GenericResultResponse:
    if principal is not None:
        # CSRF is only meaningful when there is an actual session to
        # protect; an already-logged-out caller has no ambient credential
        # a forged request could ride, so the check does not block the
        # idempotent no-op path above.
        require_csrf_token(request, request.cookies.get(CSRF_COOKIE_NAME))
        auth_service.revoke_session(
            db, user_id=principal.user_id, session_id=principal.session_id, reason="logout"
        )
        logger.info("auth.logout.success user_id=%s", principal.user_id)
    _clear_session_cookies(response)
    return GenericResultResponse()


@router.get("/me", response_model=MeResponse)
def get_current_session_info(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> MeResponse:
    user = db.get(User, principal.user_id)
    assert user is not None
    assignments = (
        db.execute(
            select(UserRoleAssignment, Role.code)
            .join(Role, Role.id == UserRoleAssignment.role_id)
            .where(UserRoleAssignment.user_id == principal.user_id)
        )
        .all()
    )
    return MeResponse(
        user=_user_out(user),
        role_assignments=[
            RoleAssignmentOut(
                role_code=role_code,
                club_id=assignment.club_id,
                scope_type=assignment.scope_type,
            )
            for assignment, role_code in assignments
        ],
    )


@router.get("/sessions", response_model=CollectionResponse[SessionOut])
def list_sessions(
    page: int = Query(default=1, ge=1),
    # Server-limited page size (api-conventions.md §9: "Default server-side
    # page size and maximum page size are global configuration values" —
    # 50/100 mirror the same defaults already used for every other
    # collection endpoint's Pagination example in this codebase).
    page_size: int = Query(default=50, ge=1, le=100),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[SessionOut]:
    rows, total = auth_service.list_sessions_page(
        db, user_id=principal.user_id, page=page, page_size=page_size
    )
    items = [
        SessionOut(
            id=row.id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            status=row.status,
            created_ip_address=row.created_ip_address,
            created_user_agent=row.created_user_agent,
            is_current=row.id == principal.session_id,
        )
        for row in rows
    ]
    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=items,
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> None:
    # auth-api.md §11: revoking another user's session needs a separate
    # permission this Issue does not grant (no canonical permission code
    # exists for it — see PR description) — scoped to the caller's own
    # sessions only. A session id that exists but belongs to someone else
    # is answered identically to one that does not exist at all.
    found = auth_service.revoke_session(
        db, user_id=principal.user_id, session_id=session_id, reason="revoked_by_user"
    )
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


@router.post("/logout-all", response_model=GenericResultResponse)
def logout_all(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GenericResultResponse:
    auth_service.logout_all(db, user_id=principal.user_id)
    logger.info("auth.logout_all.success user_id=%s", principal.user_id)
    return GenericResultResponse()


@router.post("/password-reset/request", response_model=GenericResultResponse)
def request_password_reset(
    payload: PasswordResetRequestRequest,
    db: Session = Depends(get_db),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> GenericResultResponse:
    _apply_rate_limit(limiter, f"password-reset-request:{payload.identifier.strip().lower()}")
    # auth-api.md §13: identical response regardless of account existence.
    auth_service.request_password_reset(db, payload.identifier)
    return GenericResultResponse()


@router.post("/password-reset/confirm", response_model=GenericResultResponse)
def confirm_password_reset(
    payload: PasswordResetConfirmRequest,
    request: Request,
    db: Session = Depends(get_db),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> GenericResultResponse:
    _apply_rate_limit(
        limiter,
        f"password-reset-confirm:{request.client.host if request.client else 'unknown'}",
    )
    try:
        auth_service.confirm_password_reset(
            db, raw_token=payload.token, new_password=payload.new_password
        )
    except auth_service.InvalidOrExpiredTokenError as exc:
        raise APIError(
            status.HTTP_400_BAD_REQUEST, "invalid_or_expired_token", "Invalid or expired token"
        ) from exc
    except WeakPasswordError as exc:
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "weak_password", str(exc)) from exc
    logger.info("auth.password_reset.success")
    return GenericResultResponse()


@router.post("/password/change", response_model=GenericResultResponse)
def change_password(
    payload: ChangePasswordRequest,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GenericResultResponse:
    try:
        auth_service.change_password(
            db,
            user_id=principal.user_id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except auth_service.IncorrectCurrentPasswordError as exc:
        raise APIError(
            status.HTTP_403_FORBIDDEN, "incorrect_current_password", "Current password is incorrect"
        ) from exc
    except WeakPasswordError as exc:
        raise APIError(status.HTTP_422_UNPROCESSABLE_ENTITY, "weak_password", str(exc)) from exc
    logger.info("auth.password_change.success user_id=%s", principal.user_id)
    return GenericResultResponse()
