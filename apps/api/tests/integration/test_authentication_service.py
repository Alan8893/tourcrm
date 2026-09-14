"""Real PostgreSQL integration tests for the Issue #33 authentication
service layer (app.authentication.service) — register, verify-email,
resend-verification, login (all account states), sessions, password
reset, and password change.

Run with a reachable PostgreSQL instance, matching
tests/integration/test_authentication.py's `requires_postgres` pattern:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.authentication import service as auth_service
from app.authentication.passwords import WeakPasswordError
from app.authentication.tokens import hash_token
from app.db.authentication import AuthenticatedSession, EmailVerificationChallenge
from app.db.identity import User
from app.db.session import session_scope

from .conftest import requires_postgres

_PASSWORD = "correcthorsebattery"


def _register(session, email: str = "alice@example.com") -> tuple[User, str]:
    return auth_service.register(
        session,
        email=email,
        password=_PASSWORD,
        first_name="Alice",
        last_name="Smith",
    )


def _register_and_activate(session, email: str = "alice@example.com") -> User:
    user, _ = _register(session, email)
    user.status = "active"
    session.commit()
    return user


# --- Registration --------------------------------------------------------


@requires_postgres
def test_register_creates_a_pending_user() -> None:
    with session_scope() as session:
        user, raw_token = _register(session)
        assert user.status == "pending"
        assert len(raw_token) > 20


@requires_postgres
def test_register_never_stores_the_plaintext_password() -> None:
    with session_scope() as session:
        user, _ = _register(session)
        assert user.password_hash is not None
        assert _PASSWORD not in user.password_hash


@requires_postgres
def test_register_rejects_a_duplicate_normalized_identifier() -> None:
    with session_scope() as session:
        _register(session, "alice@example.com")
        with pytest.raises(auth_service.EmailAlreadyRegisteredError):
            _register(session, " Alice@Example.com ")


@requires_postgres
def test_register_rejects_a_weak_password() -> None:
    with session_scope() as session:
        with pytest.raises(WeakPasswordError):
            auth_service.register(
                session, email="weak@example.com", password="a",
                first_name="A", last_name="B",
            )


@requires_postgres
def test_register_creates_exactly_one_outstanding_verification_challenge() -> None:
    with session_scope() as session:
        user, raw_token = _register(session)
        challenges = session.execute(
            select(EmailVerificationChallenge).where(
                EmailVerificationChallenge.user_id == user.id
            )
        ).scalars().all()
        assert len(challenges) == 1
        assert challenges[0].token_hash == hash_token(raw_token)
        assert challenges[0].consumed_at is None
        assert challenges[0].revoked_at is None


@requires_postgres
def test_concurrent_duplicate_registration_is_rejected_not_crashed() -> None:
    """Real race: two threads each get their own DB session/connection and
    both call register() with the SAME identifier, released at the same
    moment via a Barrier. Whichever request's pre-check SELECT runs first
    is not guaranteed — that is the actual race register() must survive.
    Exactly one must succeed; the other must fail with
    EmailAlreadyRegisteredError (translated from the database's own
    unique-constraint violation), never an unhandled IntegrityError/crash,
    and never both succeeding.
    """
    email = f"racer-{uuid.uuid4().hex[:8]}@example.com"
    barrier = threading.Barrier(2)
    results: list[tuple[str, object]] = []
    results_lock = threading.Lock()

    def _attempt(label: str) -> None:
        barrier.wait()
        try:
            with session_scope() as session:
                user, _ = _register(session, email)
            with results_lock:
                results.append((label, user))
        except auth_service.EmailAlreadyRegisteredError as exc:
            with results_lock:
                results.append((label, exc))
        except Exception as exc:  # noqa: BLE001 - deliberately catch-all: a
            # crash here (e.g. a raw IntegrityError escaping) is exactly
            # the bug this test exists to catch.
            with results_lock:
                results.append((label, exc))

    threads = [
        threading.Thread(target=_attempt, args=("a",)),
        threading.Thread(target=_attempt, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(results) == 2
    successes = [r for _, r in results if isinstance(r, User)]
    conflicts = [r for _, r in results if isinstance(r, auth_service.EmailAlreadyRegisteredError)]
    crashes = [
        (label, r)
        for label, r in results
        if not isinstance(r, (User, auth_service.EmailAlreadyRegisteredError))
    ]
    assert crashes == []
    assert len(successes) == 1
    assert len(conflicts) == 1

    # Exactly one row exists — no duplicate slipped through, and the
    # "loser" session was left usable (proven by a fresh, unrelated
    # registration succeeding against it below).
    with session_scope() as session:
        normalized = email.strip().lower()
        rows = session.execute(
            select(User).where(User.normalized_login_identifier == normalized)
        ).scalars().all()
        assert len(rows) == 1

        other_user, _ = _register(session, f"after-race-{uuid.uuid4().hex[:8]}@example.com")
        assert other_user.status == "pending"


# --- Email verification ----------------------------------------------------


@requires_postgres
def test_verify_email_marks_the_user_verified() -> None:
    with session_scope() as session:
        user, raw_token = _register(session)
        auth_service.verify_email(session, raw_token)
        assert user.email_verified_at is not None


@requires_postgres
def test_verify_email_token_is_single_use() -> None:
    with session_scope() as session:
        _, raw_token = _register(session)
        auth_service.verify_email(session, raw_token)
        with pytest.raises(auth_service.InvalidOrExpiredTokenError):
            auth_service.verify_email(session, raw_token)


@requires_postgres
def test_verify_email_rejects_an_unknown_token() -> None:
    with session_scope() as session:
        with pytest.raises(auth_service.InvalidOrExpiredTokenError):
            auth_service.verify_email(session, "not-a-real-token")


@requires_postgres
def test_verify_email_rejects_an_expired_token() -> None:
    with session_scope() as session:
        user, raw_token = _register(session)
        challenge = session.execute(
            select(EmailVerificationChallenge).where(
                EmailVerificationChallenge.user_id == user.id
            )
        ).scalar_one()
        challenge.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        with pytest.raises(auth_service.InvalidOrExpiredTokenError):
            auth_service.verify_email(session, raw_token)


@requires_postgres
def test_resend_verification_is_silent_for_an_unknown_identifier() -> None:
    with session_scope() as session:
        result = auth_service.resend_verification(session, "nobody@example.com")
        assert result is None


@requires_postgres
def test_resend_verification_supersedes_the_previous_challenge() -> None:
    with session_scope() as session:
        user, first_token = _register(session)
        second_token = auth_service.resend_verification(session, "alice@example.com")
        assert second_token is not None
        assert second_token != first_token

        # The first token must no longer work...
        with pytest.raises(auth_service.InvalidOrExpiredTokenError):
            auth_service.verify_email(session, first_token)
        # ...but the second (freshest) one still does.
        auth_service.verify_email(session, second_token)
        assert user.email_verified_at is not None


@requires_postgres
def test_resend_verification_is_a_noop_for_an_already_verified_user() -> None:
    with session_scope() as session:
        _, raw_token = _register(session)
        auth_service.verify_email(session, raw_token)
        result = auth_service.resend_verification(session, "alice@example.com")
        assert result is None


# --- Login / account states -------------------------------------------------


@requires_postgres
def test_login_succeeds_for_an_active_account() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        logged_in_user, raw_session_token, expires_at = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        assert logged_in_user.id == user.id
        assert len(raw_session_token) > 20
        assert expires_at > datetime.now(timezone.utc)


@requires_postgres
def test_login_updates_last_login_at() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        assert user.last_login_at is None
        auth_service.login(session, identifier="alice@example.com", password=_PASSWORD)
        assert user.last_login_at is not None


@requires_postgres
def test_login_rejects_wrong_password() -> None:
    with session_scope() as session:
        _register_and_activate(session)
        with pytest.raises(auth_service.InvalidCredentialsError):
            auth_service.login(session, identifier="alice@example.com", password="wrong")


@requires_postgres
def test_login_rejects_unknown_identifier_with_the_same_error_as_wrong_password() -> None:
    with session_scope() as session:
        with pytest.raises(auth_service.InvalidCredentialsError):
            auth_service.login(session, identifier="nobody@example.com", password="whatever1")


@pytest.mark.parametrize("status", ["pending", "locked", "suspended", "disabled", "archived"])
@requires_postgres
def test_login_rejects_every_non_active_account_state(status: str) -> None:
    with session_scope() as session:
        user, _ = _register(session)
        user.status = status
        session.commit()
        with pytest.raises(auth_service.AccountNotActiveError) as exc_info:
            auth_service.login(session, identifier="alice@example.com", password=_PASSWORD)
        assert exc_info.value.status == status


@requires_postgres
def test_login_creates_an_active_authenticated_session_row() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        row = session.execute(
            select(AuthenticatedSession).where(
                AuthenticatedSession.session_token_hash == hash_token(raw_session_token)
            )
        ).scalar_one()
        assert row.user_id == user.id
        assert row.status == "active"


# --- Session resolution / revocation ----------------------------------------


@requires_postgres
def test_resolve_session_returns_the_owning_user() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        resolved = auth_service.resolve_session(session, raw_session_token)
        assert resolved is not None
        assert resolved.user_id == user.id


@requires_postgres
def test_resolve_session_rejects_an_unknown_token() -> None:
    with session_scope() as session:
        assert auth_service.resolve_session(session, "not-a-real-session-token") is None


@requires_postgres
def test_resolve_session_rejects_an_expired_session() -> None:
    with session_scope() as session:
        _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        row = session.execute(
            select(AuthenticatedSession).where(
                AuthenticatedSession.session_token_hash == hash_token(raw_session_token)
            )
        ).scalar_one()
        # Must stay > created_at (DB CHECK constraint) while still being
        # in the past by the time resolve_session() runs below.
        row.expires_at = row.created_at + timedelta(microseconds=1)
        session.commit()
        time.sleep(0.01)
        assert auth_service.resolve_session(session, raw_session_token) is None


@requires_postgres
def test_resolve_session_still_works_while_the_account_stays_active() -> None:
    with session_scope() as session:
        _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        assert auth_service.resolve_session(session, raw_session_token) is not None


@pytest.mark.parametrize(
    "new_status", ["pending", "locked", "suspended", "disabled", "archived"]
)
@requires_postgres
def test_resolve_session_rejects_a_session_once_the_account_leaves_active(
    new_status: str,
) -> None:
    """Review finding: a session created while the account was `active`
    must stop authenticating the moment the account moves to any other
    state — resolve_session must re-check the CURRENT User.status on
    every call, not just at login time."""
    with session_scope() as session:
        user = _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        assert auth_service.resolve_session(session, raw_session_token) is not None

        user.status = new_status
        session.commit()

        assert auth_service.resolve_session(session, raw_session_token) is None


@requires_postgres
def test_resolve_session_revokes_the_session_once_the_account_leaves_active() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        user.status = "disabled"
        session.commit()
        assert auth_service.resolve_session(session, raw_session_token) is None

        row = session.execute(
            select(AuthenticatedSession).where(
                AuthenticatedSession.session_token_hash == hash_token(raw_session_token)
            )
        ).scalar_one()
        assert row.status == "revoked"
        assert row.revoked_at is not None


@requires_postgres
def test_restoring_the_account_to_active_does_not_resurrect_an_old_revoked_session() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        user.status = "suspended"
        session.commit()
        assert auth_service.resolve_session(session, raw_session_token) is None

        # The account is restored...
        user.status = "active"
        session.commit()

        # ...but the OLD session must stay dead; only a fresh login (a new
        # AuthenticatedSession row) authenticates again.
        assert auth_service.resolve_session(session, raw_session_token) is None

        _, new_raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        assert auth_service.resolve_session(session, new_raw_session_token) is not None


@requires_postgres
def test_revoke_session_invalidates_it() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        resolved = auth_service.resolve_session(session, raw_session_token)
        assert auth_service.revoke_session(
            session, user_id=user.id, session_id=resolved.session_id, reason="test"
        ) is True
        assert auth_service.resolve_session(session, raw_session_token) is None


@requires_postgres
def test_revoke_session_is_idempotent() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        resolved = auth_service.resolve_session(session, raw_session_token)
        auth_service.revoke_session(
            session, user_id=user.id, session_id=resolved.session_id, reason="test"
        )
        # Revoking again must not raise and must still report success.
        assert auth_service.revoke_session(
            session, user_id=user.id, session_id=resolved.session_id, reason="test"
        ) is True


@requires_postgres
def test_revoke_session_cannot_revoke_another_users_session() -> None:
    with session_scope() as session:
        _register_and_activate(session, "alice@example.com")
        _register_and_activate(session, "carol@example.com")
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        alice_session = auth_service.resolve_session(session, raw_session_token)
        carol = session.execute(
            select(User).where(User.login_identifier == "carol@example.com")
        ).scalar_one()

        found = auth_service.revoke_session(
            session, user_id=carol.id, session_id=alice_session.session_id, reason="test"
        )
        assert found is False
        # Alice's session must still be usable — Carol's attempt was a no-op.
        assert auth_service.resolve_session(session, raw_session_token) is not None


@requires_postgres
def test_logout_all_revokes_every_active_session() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        _, token_a, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        _, token_b, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        auth_service.logout_all(session, user_id=user.id)
        assert auth_service.resolve_session(session, token_a) is None
        assert auth_service.resolve_session(session, token_b) is None


@requires_postgres
def test_list_sessions_returns_one_row_per_active_session() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        auth_service.login(session, identifier="alice@example.com", password=_PASSWORD)
        auth_service.login(session, identifier="alice@example.com", password=_PASSWORD)
        rows = auth_service.list_sessions(session, user_id=user.id)
        assert len(rows) == 2
        # Safe-serialization itself is the API schema's job (SessionOut has
        # no token_hash field at all) — see tests/integration/test_authentication_api.py.


# --- Password reset / change -------------------------------------------------


@requires_postgres
def test_request_password_reset_is_silent_for_an_unknown_identifier() -> None:
    with session_scope() as session:
        assert auth_service.request_password_reset(session, "nobody@example.com") is None


@requires_postgres
def test_confirm_password_reset_changes_the_password_and_is_single_use() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        old_hash = user.password_hash
        raw_token = auth_service.request_password_reset(session, "alice@example.com")
        assert raw_token is not None

        auth_service.confirm_password_reset(
            session, raw_token=raw_token, new_password="newpassword123"
        )
        assert user.password_hash != old_hash

        with pytest.raises(auth_service.InvalidOrExpiredTokenError):
            auth_service.confirm_password_reset(
                session, raw_token=raw_token, new_password="anothernewpass1"
            )


@requires_postgres
def test_confirm_password_reset_invalidates_existing_sessions() -> None:
    with session_scope() as session:
        _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        raw_reset_token = auth_service.request_password_reset(session, "alice@example.com")
        auth_service.confirm_password_reset(
            session, raw_token=raw_reset_token, new_password="newpassword123"
        )
        assert auth_service.resolve_session(session, raw_session_token) is None


@requires_postgres
def test_confirm_password_reset_rejects_a_weak_new_password_without_burning_the_token() -> None:
    with session_scope() as session:
        _register_and_activate(session)
        raw_token = auth_service.request_password_reset(session, "alice@example.com")

        with pytest.raises(WeakPasswordError):
            auth_service.confirm_password_reset(session, raw_token=raw_token, new_password="a")

        # Token must still be valid — the weak-password rejection happened
        # before consumption.
        auth_service.confirm_password_reset(
            session, raw_token=raw_token, new_password="perfectlyvalidpassword"
        )


@requires_postgres
def test_change_password_requires_the_correct_current_password() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        with pytest.raises(auth_service.IncorrectCurrentPasswordError):
            auth_service.change_password(
                session,
                user_id=user.id,
                current_password="wrong-current-password",
                new_password="newpassword123",
            )


@requires_postgres
def test_change_password_succeeds_and_does_not_revoke_other_sessions() -> None:
    with session_scope() as session:
        user = _register_and_activate(session)
        _, raw_session_token, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )
        old_hash = user.password_hash
        auth_service.change_password(
            session,
            user_id=user.id,
            current_password=_PASSWORD,
            new_password="newpassword123",
        )
        assert user.password_hash != old_hash
        # auth-api.md §14 requires invalidation only for a RESET, not for
        # an in-session change.
        assert auth_service.resolve_session(session, raw_session_token) is not None


# --- Regression: no client-supplied id can select a principal --------------


@requires_postgres
def test_resolve_session_ignores_everything_but_the_raw_token_itself() -> None:
    """There is no parameter anywhere in resolve_session by which a caller
    can assert a user id directly — the only input is the opaque secret.
    This test constructs a session for user A and confirms that even a
    real, existing user B's id is never consulted or returned unless A's
    own raw token is presented."""
    with session_scope() as session:
        user_a = _register_and_activate(session, "alice@example.com")
        user_b = _register_and_activate(session, "carol@example.com")
        _, raw_token_a, _ = auth_service.login(
            session, identifier="alice@example.com", password=_PASSWORD
        )

        resolved = auth_service.resolve_session(session, raw_token_a)
        assert resolved is not None
        assert resolved.user_id == user_a.id
        assert resolved.user_id != user_b.id

        # A syntactically-plausible but fabricated token must resolve to
        # nobody — not user_b, not anyone.
        fabricated = hash_token(str(uuid.uuid4()))
        assert auth_service.resolve_session(session, fabricated) is None
