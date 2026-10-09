"""Migration ddc70357f0cb (Issue #336 PR-0, ADR-0049): upgrade ->
downgrade -> upgrade round trip, existing per-event preferences becoming
personal (`destination_type = user`), and a downgrade that refuses —
changing nothing — instead of deleting a route-addressed Notification."""

import uuid

import sqlalchemy as sa

from app.db.session import get_engine

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_REVISION = "ddc70357f0cb"
_PARENT = "bef882c4e71c"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(get_engine()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(get_engine()).get_table_names())


def _user_id(connection: sa.Connection) -> uuid.UUID:
    person_id, user_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(
        sa.text("INSERT INTO persons (id, last_name, first_name) VALUES (:id, 'M', 'T')"),
        {"id": person_id},
    )
    connection.execute(
        sa.text(
            "INSERT INTO users (id, person_id, login_identifier, status) "
            "VALUES (:id, :person, :login, 'active')"
        ),
        {"id": user_id, "person": person_id, "login": f"m-{user_id.hex[:8]}@club.test"},
    )
    return user_id


@requires_postgres
def test_round_trip_keeps_existing_preferences_as_personal(database_url: str) -> None:
    try:
        downgrade = run_alembic("downgrade", _PARENT, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr
        assert "communication_channel_preferences" not in _tables()
        assert "destination_type" not in _columns("communication_preferences")
        assert not {"render_context", "recipient_destination_id"} & _columns("notifications")
        with get_engine().begin() as connection:
            user_id = _user_id(connection)
            connection.execute(
                sa.text(
                    "INSERT INTO communication_preferences "
                    "(id, user_id, channel, notification_type, enabled) "
                    "VALUES (:id, :user, 'telegram', 'news.published', true)"
                ),
                {"id": uuid.uuid4(), "user": user_id},
            )
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr
    assert "communication_channel_preferences" in _tables()
    assert {"render_context", "recipient_destination_id"} <= _columns("notifications")
    with get_engine().connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT destination_type, enabled FROM communication_preferences "
                "WHERE user_id = :user"
            ),
            {"user": user_id},
        ).all()
    assert [tuple(row) for row in rows] == [("user", True)]
    check = run_alembic("check", database_url=database_url)
    assert check.returncode == 0, check.stderr


@requires_postgres
def test_downgrade_refuses_while_a_route_addressed_notification_exists(
    database_url: str,
) -> None:
    destination_id, notification_id = uuid.uuid4(), uuid.uuid4()
    with get_engine().begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO telegram_destinations (id, name, chat_id) "
                "VALUES (:id, 'Club', -100123)"
            ),
            {"id": destination_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO notifications (id, event_type, subject_type, "
                "recipient_destination_id, idempotency_key) "
                "VALUES (:id, 'event.cancelled', 'event', :dest, :key)"
            ),
            {"id": notification_id, "dest": destination_id, "key": f"k-{notification_id}"},
        )
    try:
        downgrade = run_alembic("downgrade", _PARENT, database_url=database_url)
        assert downgrade.returncode != 0
        assert "downgrade refused" in downgrade.stderr
        # Transactional DDL: nothing changed, the row is still there.
        assert "communication_channel_preferences" in _tables()
        with get_engine().connect() as connection:
            assert connection.execute(
                sa.text("SELECT count(*) FROM notifications WHERE id = :id"),
                {"id": notification_id},
            ).scalar_one() == 1
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
    assert upgrade.returncode == 0, upgrade.stderr


@requires_postgres
def test_constraints_reject_ambiguous_recipients_and_bad_shapes() -> None:
    with get_engine().connect() as connection:
        user_id = _user_id(connection)
        destination_id = uuid.uuid4()
        connection.execute(
            sa.text(
                "INSERT INTO telegram_destinations (id, name, chat_id) VALUES (:id, 'C', -1001)"
            ),
            {"id": destination_id},
        )
        cases = [
            (
                "INSERT INTO notifications (id, event_type, subject_type, idempotency_key) "
                "VALUES (gen_random_uuid(), 'e', 's', 'k1')",
                "ck_notifications_exactly_one_recipient",
            ),
            (
                "INSERT INTO notifications (id, event_type, subject_type, recipient_user_id, "
                "recipient_destination_id, idempotency_key) "
                f"VALUES (gen_random_uuid(), 'e', 's', '{user_id}', '{destination_id}', 'k2')",
                "ck_notifications_exactly_one_recipient",
            ),
            (
                "INSERT INTO notifications (id, event_type, subject_type, recipient_user_id, "
                "idempotency_key, render_context) "
                f"VALUES (gen_random_uuid(), 'e', 's', '{user_id}', 'k3', '[1]'::jsonb)",
                "ck_notifications_render_context_is_object",
            ),
            (
                "INSERT INTO communication_preferences (id, user_id, channel, destination_type, "
                "notification_type, enabled) VALUES (gen_random_uuid(), "
                f"'{user_id}', 'telegram', 'telegram_destination', 'e', true)",
                "ck_communication_preferences_destination_type_valid",
            ),
            (
                "INSERT INTO communication_channel_preferences (id, user_id, channel, "
                "destination_type, enabled) VALUES (gen_random_uuid(), "
                f"'{user_id}', 'telegram', 'telegram_destination', true)",
                "ck_communication_channel_preferences_destination_type_valid",
            ),
            (
                "UPDATE telegram_destinations SET notification_scope = "
                "'{\"event_types\": \"event.cancelled\"}'::jsonb WHERE id = "
                f"'{destination_id}'",
                "ck_telegram_destinations_event_types_is_array",
            ),
        ]
        for statement, constraint in cases:
            savepoint = connection.begin_nested()
            try:
                connection.execute(sa.text(statement))
            except sa.exc.IntegrityError as exc:
                assert constraint in str(exc.orig)
            else:
                raise AssertionError(f"{constraint} did not reject the row")
            finally:
                savepoint.rollback()
        connection.rollback()
