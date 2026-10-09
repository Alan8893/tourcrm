"""PostgreSQL integration tests for Telegram account linking (Issue #329,
ADR-0047 §4): challenge issuance (hash-only storage, deep-link
constraints, reissue/revoke, rate limit), consumption (expiry, single use,
replay, indistinguishable rejections, inactive owner), identity invariants
(uniqueness, no silent transfer, replacement, unlink/relink), concurrency,
rollback, safe audit and the migration round trip.

Self-contained factories, per this codebase's convention of not importing
helpers across test files.
"""

import re
import threading
import uuid
from datetime import timedelta
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.authentication.tokens import hash_token
from app.db.audit import AuditLog
from app.db.identity import Person, User
from app.db.session import get_engine, session_scope
from app.db.telegram import TelegramIdentity, TelegramLinkChallenge
from app.telegram import linking
from app.telegram.updates import REPLY_LINKED, REPLY_REJECTED, handle_update
from app.telegram.vocabulary import CHALLENGE_RATE_LIMIT_COUNT
from tests.telegram_fakes import BOT_USERNAME, group_message, private_start

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_MIGRATION = "162f2e413172"
_MIGRATION_PARENT = "8c7a78e7178a"
_NEW_TABLES = {"telegram_identities", "telegram_link_challenges", "telegram_update_checkpoints"}

ANNA_TG = 5_000_000_001
BORIS_TG = 5_000_000_002


def _user(status: str = "active") -> uuid.UUID:
    with session_scope() as session:
        person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person,
            login_identifier=f"u-{uuid.uuid4().hex[:8]}@club.test",
            status=status,
        )
        session.add(user)
        session.commit()
        return user.id


def _issue(user_id: uuid.UUID) -> str:
    """Issue a challenge and return its raw token (from the deep link)."""
    with session_scope() as session:
        issued = linking.create_link_challenge(
            session, user_id=user_id, bot_username=BOT_USERNAME
        )
        session.commit()
    (token,) = parse_qs(urlparse(issued.deep_link).query)["start"]
    return token


def _consume(token: str, telegram_user_id: int) -> str:
    with session_scope() as session:
        outcome = linking.consume_link_challenge(
            session, raw_token=token, telegram_user_id=telegram_user_id
        )
        session.commit()
        return outcome


def _active(user_id: uuid.UUID) -> list[TelegramIdentity]:
    with session_scope() as session:
        return list(
            session.execute(
                sa.select(TelegramIdentity).where(
                    TelegramIdentity.user_id == user_id, TelegramIdentity.status == "active"
                )
            ).scalars()
        )


def _identities(user_id: uuid.UUID) -> list[TelegramIdentity]:
    with session_scope() as session:
        return list(
            session.execute(
                sa.select(TelegramIdentity)
                .where(TelegramIdentity.user_id == user_id)
                .order_by(TelegramIdentity.created_at, TelegramIdentity.status)
            ).scalars()
        )


def _challenges(user_id: uuid.UUID) -> list[TelegramLinkChallenge]:
    with session_scope() as session:
        return list(
            session.execute(
                sa.select(TelegramLinkChallenge)
                .where(TelegramLinkChallenge.user_id == user_id)
                .order_by(TelegramLinkChallenge.created_at)
            ).scalars()
        )


def _expire(token: str) -> None:
    with session_scope() as session:
        session.execute(
            sa.update(TelegramLinkChallenge)
            .where(TelegramLinkChallenge.token_hash == hash_token(token))
            .values(
                created_at=sa.func.now() - timedelta(hours=1),
                expires_at=sa.func.now() - timedelta(seconds=1),
            )
        )
        session.commit()


# --- Issuance ------------------------------------------------------------------------------


@requires_postgres
def test_challenge_deep_link_and_hash_only_storage() -> None:
    user_id = _user()
    with session_scope() as session:
        issued = linking.create_link_challenge(
            session, user_id=user_id, bot_username=BOT_USERNAME
        )
        session.commit()
    parsed = urlparse(issued.deep_link)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "t.me", f"/{BOT_USERNAME}")
    (token,) = parse_qs(parsed.query)["start"]
    # Telegram deep-link start parameter constraints.
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", token)
    assert len(token) >= 40
    assert token not in repr(issued)

    (row,) = _challenges(user_id)
    assert row.token_hash == hash_token(token)
    assert row.status == "pending"
    assert timedelta(minutes=14) < row.expires_at - row.created_at <= timedelta(minutes=15)
    with session_scope() as session:
        dumped = " ".join(
            str(value)
            for table in ("telegram_link_challenges", "audit_logs")
            for record in session.execute(sa.text(f"SELECT * FROM {table}")).all()
            for value in record
        )
    assert token not in dumped


@requires_postgres
def test_tokens_are_unique_per_issue() -> None:
    user_id = _user()
    tokens = {_issue(user_id) for _ in range(3)}
    assert len(tokens) == 3


@requires_postgres
def test_reissue_revokes_the_previous_pending_challenge() -> None:
    user_id = _user()
    first = _issue(user_id)
    second = _issue(user_id)
    statuses = [(row.status, row.revoked_at is not None) for row in _challenges(user_id)]
    assert statuses == [("revoked", True), ("pending", False)]
    assert _consume(first, ANNA_TG) == linking.LINK_REJECTED
    assert _consume(second, ANNA_TG) == linking.LINKED


@requires_postgres
def test_issuance_is_rate_limited_per_user() -> None:
    user_id, other = _user(), _user()
    for _ in range(CHALLENGE_RATE_LIMIT_COUNT):
        _issue(user_id)
    with session_scope() as session:
        with pytest.raises(linking.LinkChallengeRateLimited):
            linking.create_link_challenge(session, user_id=user_id, bot_username=BOT_USERNAME)
        session.rollback()
    assert len(_challenges(user_id)) == CHALLENGE_RATE_LIMIT_COUNT
    # Another user's budget is independent.
    _issue(other)


@requires_postgres
def test_rate_limit_window_is_rolling() -> None:
    user_id = _user()
    for _ in range(CHALLENGE_RATE_LIMIT_COUNT):
        _issue(user_id)
    with session_scope() as session:
        session.execute(
            sa.update(TelegramLinkChallenge)
            .where(TelegramLinkChallenge.user_id == user_id)
            .values(
                created_at=sa.func.now() - timedelta(hours=2),
                expires_at=sa.func.now() - timedelta(hours=1),
            )
        )
        session.commit()
    _issue(user_id)


@requires_postgres
def test_concurrent_issuance_cannot_exceed_the_rate_limit() -> None:
    user_id = _user()
    results = _run_concurrently(
        [lambda: _try_issue(user_id) for _ in range(CHALLENGE_RATE_LIMIT_COUNT + 3)]
    )
    assert results.count("issued") == CHALLENGE_RATE_LIMIT_COUNT
    assert results.count("limited") == 3
    pending = [row for row in _challenges(user_id) if row.status == "pending"]
    assert len(pending) == 1


def _try_issue(user_id: uuid.UUID) -> str:
    try:
        _issue(user_id)
    except linking.LinkChallengeRateLimited:
        return "limited"
    return "issued"


@requires_postgres
def test_inactive_user_cannot_issue() -> None:
    user_id = _user(status="locked")
    with session_scope() as session:
        with pytest.raises(linking.LinkingUnavailable):
            linking.create_link_challenge(session, user_id=user_id, bot_username=BOT_USERNAME)
    assert _challenges(user_id) == []


# --- Consumption ---------------------------------------------------------------------------


@requires_postgres
def test_consuming_links_the_trusted_sender_to_the_challenge_owner() -> None:
    user_id = _user()
    token = _issue(user_id)
    assert _consume(token, ANNA_TG) == linking.LINKED
    (identity,) = _active(user_id)
    assert identity.telegram_user_id == ANNA_TG
    (challenge,) = _challenges(user_id)
    assert (challenge.status, challenge.telegram_identity_id) == ("consumed", identity.id)
    assert challenge.consumed_at is not None
    with session_scope() as session:
        status = linking.get_link_status(session, user_id=user_id)
    assert status.linked and status.linked_at == identity.linked_at


@requires_postgres
def test_challenge_is_single_use_and_replay_is_rejected() -> None:
    user_id = _user()
    token = _issue(user_id)
    assert _consume(token, ANNA_TG) == linking.LINKED
    assert _consume(token, ANNA_TG) == linking.LINK_REJECTED
    assert _consume(token, BORIS_TG) == linking.LINK_REJECTED
    assert [row.telegram_user_id for row in _identities(user_id)] == [ANNA_TG]


@requires_postgres
def test_expired_challenge_is_rejected_by_database_time() -> None:
    user_id = _user()
    token = _issue(user_id)
    _expire(token)
    assert _consume(token, ANNA_TG) == linking.LINK_REJECTED
    assert _identities(user_id) == []
    (challenge,) = _challenges(user_id)
    assert challenge.status == "pending"


@requires_postgres
def test_rejections_are_indistinguishable_to_the_telegram_user() -> None:
    owner, holder = _user(), _user()
    consumed = _issue(owner)
    _consume(consumed, ANNA_TG)
    revoked = _issue(holder)
    _issue(holder)
    expired_owner = _user()
    expired = _issue(expired_owner)
    _expire(expired)
    conflicting_owner = _user()
    conflicting = _issue(conflicting_owner)
    inactive_owner = _user()
    inactive = _issue(inactive_owner)
    with session_scope() as session:
        session.execute(sa.update(User).where(User.id == inactive_owner).values(status="locked"))
        session.commit()

    cases = {
        "unknown": ("A" * 43, BORIS_TG),
        "malformed": ("not a token!", BORIS_TG),
        "too_long": ("A" * 65, BORIS_TG),
        "consumed": (consumed, BORIS_TG),
        "revoked": (revoked, BORIS_TG),
        "expired": (expired, BORIS_TG),
        # ANNA_TG is actively linked to `owner`, not to this challenge's owner.
        "identity_conflict": (conflicting, ANNA_TG),
        "inactive_owner": (inactive, BORIS_TG),
    }
    replies = {}
    for name, (token, sender) in cases.items():
        with session_scope() as session:
            result = handle_update(session, private_start(1, sender, f"/start {token}"))
            session.commit()
        replies[name] = (result.outcome, result.reply_text)
    assert set(replies.values()) == {("rejected", REPLY_REJECTED)}
    for user_id in (holder, expired_owner, conflicting_owner, inactive_owner):
        assert _identities(user_id) == []


@requires_postgres
def test_group_message_cannot_link() -> None:
    user_id = _user()
    token = _issue(user_id)
    with session_scope() as session:
        result = handle_update(session, group_message(1, ANNA_TG, f"/start {token}"))
        session.commit()
    assert result.outcome == "ignored" and result.reply_text is None
    assert _identities(user_id) == []
    assert _consume(token, ANNA_TG) == linking.LINKED


@requires_postgres
def test_private_start_reply_never_echoes_the_token() -> None:
    user_id = _user()
    token = _issue(user_id)
    with session_scope() as session:
        result = handle_update(session, private_start(1, ANNA_TG, f"/start {token}"))
        session.commit()
    assert (result.outcome, result.reply_chat_id, result.reply_text) == (
        "linked",
        ANNA_TG,
        REPLY_LINKED,
    )
    assert token not in (result.reply_text or "")


@requires_postgres
def test_rollback_of_the_surrounding_transaction_leaves_no_link() -> None:
    user_id = _user()
    token = _issue(user_id)
    with session_scope() as session:
        assert (
            linking.consume_link_challenge(session, raw_token=token, telegram_user_id=ANNA_TG)
            == linking.LINKED
        )
        session.rollback()
    assert _identities(user_id) == []
    (challenge,) = _challenges(user_id)
    assert challenge.status == "pending"
    assert _consume(token, ANNA_TG) == linking.LINKED


# --- Identity invariants -------------------------------------------------------------------


@requires_postgres
def test_identity_linked_to_another_user_is_never_transferred() -> None:
    anna, boris = _user(), _user()
    _consume(_issue(anna), ANNA_TG)
    boris_token = _issue(boris)
    assert _consume(boris_token, ANNA_TG) == linking.LINK_REJECTED
    assert [row.telegram_user_id for row in _active(anna)] == [ANNA_TG]
    assert _active(boris) == []
    # Boris's challenge is untouched: he can still use it from his own account.
    assert _consume(boris_token, BORIS_TG) == linking.LINKED
    with session_scope() as session:
        failure = session.execute(
            sa.select(AuditLog).where(
                AuditLog.action == "telegram_identity.linked", AuditLog.outcome == "failure"
            )
        ).scalar_one()
    assert failure.actor_user_id == boris
    assert failure.details == {"reason": "identity_linked_to_another_user"}


@requires_postgres
def test_relinking_the_same_account_is_an_idempotent_confirmation() -> None:
    user_id = _user()
    _consume(_issue(user_id), ANNA_TG)
    assert _consume(_issue(user_id), ANNA_TG) == linking.ALREADY_LINKED
    (identity,) = _identities(user_id)
    assert identity.status == "active"
    assert all(row.status == "consumed" for row in _challenges(user_id))


@requires_postgres
def test_linking_a_different_account_replaces_the_previous_identity() -> None:
    user_id = _user()
    _consume(_issue(user_id), ANNA_TG)
    assert _consume(_issue(user_id), BORIS_TG) == linking.LINKED
    old, new = _identities(user_id)
    assert (old.telegram_user_id, old.status) == (ANNA_TG, "replaced")
    assert old.ended_at is not None
    assert (new.telegram_user_id, new.status) == (BORIS_TG, "active")
    # The replaced Telegram account is free for another User now.
    other = _user()
    assert _consume(_issue(other), ANNA_TG) == linking.LINKED


@requires_postgres
def test_unlink_ends_the_identity_and_revokes_pending_challenges() -> None:
    user_id = _user()
    _consume(_issue(user_id), ANNA_TG)
    pending = _issue(user_id)
    with session_scope() as session:
        assert linking.unlink_telegram_identity(session, user_id=user_id) is True
        session.commit()
    (identity,) = _identities(user_id)
    assert (identity.status, identity.ended_at is not None) == ("unlinked", True)
    assert _challenges(user_id)[-1].status == "revoked"
    assert _consume(pending, ANNA_TG) == linking.LINK_REJECTED
    with session_scope() as session:
        assert linking.unlink_telegram_identity(session, user_id=user_id) is False
        assert linking.get_link_status(session, user_id=user_id).linked is False
    # After unlinking, the Telegram account may be linked to another User.
    other = _user()
    assert _consume(_issue(other), ANNA_TG) == linking.LINKED
    # ... and the first User can link again with a fresh challenge.
    assert _consume(_issue(user_id), BORIS_TG) == linking.LINKED


@requires_postgres
def test_database_enforces_one_active_identity_per_telegram_user_and_per_user() -> None:
    anna, boris = _user(), _user()
    with session_scope() as session:
        session.add(TelegramIdentity(user_id=anna, telegram_user_id=ANNA_TG, status="active"))
        session.commit()
    for user_id, telegram_user_id in ((boris, ANNA_TG), (anna, BORIS_TG)):
        with session_scope() as session:
            session.add(
                TelegramIdentity(
                    user_id=user_id, telegram_user_id=telegram_user_id, status="active"
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
    # Ended rows are history and do not collide.
    with session_scope() as session:
        session.add(
            TelegramIdentity(
                user_id=boris,
                telegram_user_id=ANNA_TG,
                status="unlinked",
                ended_at=sa.func.now(),
            )
        )
        session.commit()


@requires_postgres
def test_database_rejects_inconsistent_challenge_lifecycle() -> None:
    user_id = _user()
    bad_rows: list[dict[str, Any]] = [
        {"status": "consumed"},
        {"status": "pending", "consumed_at": sa.func.now()},
        {"status": "revoked"},
        {"status": "pending", "expires_at": sa.func.now() - timedelta(minutes=1)},
        {"status": "expired"},
    ]
    for overrides in bad_rows:
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "token_hash": uuid.uuid4().hex,
            "status": "pending",
            "expires_at": sa.func.now() + timedelta(minutes=5),
        }
        values.update(overrides)
        with session_scope() as session:
            with pytest.raises(IntegrityError):
                session.execute(sa.insert(TelegramLinkChallenge).values(**values))
                session.commit()


# --- Concurrency ---------------------------------------------------------------------------


def _run_concurrently(tasks: list[Callable[[], Any]]) -> list[Any]:
    barrier = threading.Barrier(len(tasks))
    results: list[Any] = [None] * len(tasks)
    errors: list[BaseException] = []

    def run(index: int, task: Callable[[], Any]) -> None:
        barrier.wait()
        try:
            results[index] = task()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(index, task)) for index, task in enumerate(tasks)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors
    return results


@requires_postgres
def test_concurrent_consumption_of_one_challenge_links_once() -> None:
    user_id = _user()
    token = _issue(user_id)
    results = _run_concurrently(
        [lambda: _consume(token, ANNA_TG), lambda: _consume(token, BORIS_TG)] * 2
    )
    assert results.count(linking.LINKED) == 1
    assert results.count(linking.LINK_REJECTED) == 3
    assert len(_identities(user_id)) == 1


@requires_postgres
def test_concurrent_links_of_one_telegram_account_to_two_users_admit_one() -> None:
    anna, boris = _user(), _user()
    anna_token, boris_token = _issue(anna), _issue(boris)
    results = _run_concurrently(
        [lambda: _consume(anna_token, ANNA_TG), lambda: _consume(boris_token, ANNA_TG)]
    )
    assert sorted(results) == sorted([linking.LINKED, linking.LINK_REJECTED])
    with session_scope() as session:
        holders = session.execute(
            sa.select(TelegramIdentity.user_id).where(
                TelegramIdentity.telegram_user_id == ANNA_TG,
                TelegramIdentity.status == "active",
            )
        ).all()
    assert len(holders) == 1


# --- Audit ---------------------------------------------------------------------------------


@requires_postgres
def test_audit_records_metadata_only() -> None:
    user_id = _user()
    first = _issue(user_id)
    _consume(first, ANNA_TG)
    second = _issue(user_id)
    _consume(second, BORIS_TG)
    with session_scope() as session:
        linking.unlink_telegram_identity(session, user_id=user_id)
        session.commit()
        rows = session.execute(
            sa.select(AuditLog).where(AuditLog.actor_user_id == user_id).order_by(AuditLog.id)
        ).scalars()
        records = [(row.action, row.outcome, row.details) for row in rows]
    actions = sorted(action for action, _, _ in records)
    assert actions == sorted(
        [
            "telegram_link_challenge.created",
            "telegram_link_challenge.created",
            "telegram_identity.linked",
            "telegram_identity.linked",
            "telegram_identity.unlinked",
            "telegram_identity.unlinked",
        ]
    )
    rendered = repr(records)
    for secret in (first, second, hash_token(first), str(ANNA_TG), str(BORIS_TG)):
        assert secret not in rendered
    assert {"reason": "replaced"} in [details for _, _, details in records]
    assert {"reason": "user_unlinked"} in [details for _, _, details in records]


# --- Migration -----------------------------------------------------------------------------


@requires_postgres
def test_migration_round_trip(database_url: str) -> None:
    try:
        downgrade = run_alembic("downgrade", _MIGRATION_PARENT, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        assert not _NEW_TABLES & set(sa.inspect(get_engine()).get_table_names())
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    assert _NEW_TABLES <= set(sa.inspect(get_engine()).get_table_names())
    columns = {
        column["name"]
        for table in _NEW_TABLES
        for column in sa.inspect(get_engine()).get_columns(table)
    }
    assert not {name for name in columns if "token" in name} - {"token_hash"}
