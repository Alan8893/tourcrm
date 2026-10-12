"""Migration b6655ba7d0c7 (Issue #342): the password-reset delivery column,
its CHECK constraint and the seeded email template.

Real `alembic` runs through the shared `run_alembic` helper; every test
leaves the schema at head.
"""

import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.db.authentication import PasswordResetChallenge
from app.db.identity import Person, User
from app.db.session import get_engine, session_scope

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_PARENT = "700a5f703346"
_TEMPLATE_ID = uuid.UUID("7fef6d48-6e6b-4aa2-8daf-c5c5311d954d")


def _alembic(*args: str, database_url: str) -> tuple[int, str]:
    result = run_alembic(*args, database_url=database_url)
    return result.returncode, result.stderr


def _state() -> tuple[bool, bool, set[uuid.UUID]]:
    with get_engine().connect() as connection:
        column = connection.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns WHERE table_name = "
                "'password_reset_challenges' AND column_name = 'delivery_secret_ciphertext'"
            )
        ).first()
        constraint = connection.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint WHERE conname = "
                "'ck_password_reset_challenges_delivery_secret_only_while_open'"
            )
        ).first()
        templates = set(
            connection.execute(
                sa.text(
                    "SELECT id FROM notification_templates "
                    "WHERE code = 'auth_password_reset_code.email'"
                )
            ).scalars()
        )
    return column is not None, constraint is not None, templates


@requires_postgres
def test_upgrade_downgrade_upgrade_and_rerun(database_url: str) -> None:
    assert _state() == (True, True, {_TEMPLATE_ID})
    try:
        assert _alembic("downgrade", _PARENT, database_url=database_url)[0] == 0
        assert _state() == (False, False, set())
    finally:
        assert _alembic("upgrade", "head", database_url=database_url)[0] == 0
    assert _state() == (True, True, {_TEMPLATE_ID})

    # Re-running the upgrade over the migrated schema changes nothing.
    assert _alembic("stamp", _PARENT, database_url=database_url)[0] == 0
    assert _alembic("upgrade", "head", database_url=database_url)[0] == 0
    assert _state() == (True, True, {_TEMPLATE_ID})


@requires_postgres
def test_downgrade_refuses_while_a_delivery_secret_is_pending(database_url: str) -> None:
    with session_scope() as session:
        user = User(
            person=Person(last_name="L", first_name="F"),
            login_identifier="m@club.test",
            status="active",
        )
        session.add(user)
        session.flush()
        session.add(
            PasswordResetChallenge(
                user_id=user.id,
                token_hash=uuid.uuid4().hex,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                delivery_secret_ciphertext="v1.k.n.c",
            )
        )
        session.commit()
    try:
        code, stderr = _alembic("downgrade", _PARENT, database_url=database_url)
        assert code != 0
        assert "pending delivery secret" in stderr
        assert _state() == (True, True, {_TEMPLATE_ID})
    finally:
        assert _alembic("upgrade", "head", database_url=database_url)[0] == 0
