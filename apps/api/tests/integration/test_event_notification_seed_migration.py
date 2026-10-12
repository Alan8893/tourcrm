"""Migration a65b609f4d34 (Issue #336 PR-1): seeding the event
cancellation/reschedule templates and rules never takes over, overwrites
or deletes configuration it did not create.

Real `alembic upgrade`/`downgrade`/`stamp` runs against PostgreSQL through
the shared `run_alembic` helper, like the other migration tests. Every
test leaves the schema at head; the integration fixtures restore the data
baseline before the next test.
"""

import uuid
from typing import Any

import sqlalchemy as sa

from app.db.session import get_engine

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_PARENT = "ddc70357f0cb"

# The migration's frozen primary keys.
_CANCELLED_TEMPLATE_ID = uuid.UUID("afb3fb04-3048-44b3-9b76-7fba5b1f66e4")
_RESCHEDULED_TEMPLATE_ID = uuid.UUID("82a8f4ac-a206-4fda-ba3e-0f3080e08500")
_RULE_IDS = {
    ("event.cancelled", "registered_participants"): uuid.UUID(
        "18d65f23-d098-469b-b25d-0e60dfa50139"
    ),
    ("event.cancelled", "telegram_destination"): uuid.UUID(
        "0a29c097-be54-4f11-aed7-23da2102065c"
    ),
    ("event.rescheduled", "registered_participants"): uuid.UUID(
        "f316ed2a-b73d-444d-a865-37f4c65bee94"
    ),
    ("event.rescheduled", "telegram_destination"): uuid.UUID(
        "73237d1a-bf3f-41e3-8eca-fc346b6c606d"
    ),
}
_SEEDED_TEMPLATES = {
    ("event_cancelled.telegram", _CANCELLED_TEMPLATE_ID, True),
    ("event_rescheduled.telegram", _RESCHEDULED_TEMPLATE_ID, True),
}
_SEEDED_RULES = {
    (event_type, scope, rule_id, False) for (event_type, scope), rule_id in _RULE_IDS.items()
}


def _alembic(*args: str, database_url: str) -> None:
    result = run_alembic(*args, database_url=database_url)
    assert result.returncode == 0, result.stderr


# Rows seeded by later migrations (PR-2 registration slice, Issue #342
# password-reset code email) are created and removed by those migrations
# whenever these tests move across them; they are not this migration's
# concern and are left out of every comparison.
_LATER_TEMPLATE_CODES = (
    "registration_created.telegram",
    "registration_cancelled.telegram",
    "auth_password_reset_code.email",
)
_LATER_RULE_EVENT_TYPES = ("registration.created", "registration.cancelled")


def _templates() -> set[tuple[str, uuid.UUID, bool]]:
    with get_engine().connect() as connection:
        return {
            (row.code, row.id, row.is_active)
            for row in connection.execute(
                sa.text("SELECT code, id, is_active FROM notification_templates")
            )
            if row.code not in _LATER_TEMPLATE_CODES
        }


def _template_bodies() -> dict[str, str]:
    with get_engine().connect() as connection:
        return dict(
            connection.execute(
                sa.text("SELECT code, body_template FROM notification_templates")
            ).all()
        )


def _rules() -> set[tuple[str, str, uuid.UUID, bool]]:
    with get_engine().connect() as connection:
        return {
            (row.event_type, row.recipient_scope, row.id, row.is_enabled)
            for row in connection.execute(
                sa.text(
                    "SELECT event_type, recipient_scope, id, is_enabled FROM notification_rules"
                )
            )
            if row.event_type not in _LATER_RULE_EVENT_TYPES
        }


def _execute(statement: str, **params: Any) -> None:
    with get_engine().begin() as connection:
        connection.execute(sa.text(statement), params)


def _existing_template(
    code: str, *, body: str = "Своя формулировка", active: bool = False
) -> uuid.UUID:
    template_id = uuid.uuid4()
    _execute(
        "INSERT INTO notification_templates (id, code, channel, locale, body_template, is_active) "
        "VALUES (:id, :code, 'telegram', 'ru', :body, :active)",
        id=template_id, code=code, body=body, active=active,
    )
    return template_id


def _existing_rule(event_type: str, scope: str, *, enabled: bool = True) -> uuid.UUID:
    rule_id = uuid.uuid4()
    _execute(
        "INSERT INTO notification_rules (id, club_id, event_type, channel, recipient_scope, "
        "is_enabled) VALUES (:id, NULL, :event_type, 'telegram', :scope, :enabled)",
        id=rule_id, event_type=event_type, scope=scope, enabled=enabled,
    )
    return rule_id


def _notification_using(template_id: uuid.UUID) -> uuid.UUID:
    user_id, person_id, notification_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _execute(
        "INSERT INTO persons (id, last_name, first_name) VALUES (:id, 'M', 'T')", id=person_id
    )
    _execute(
        "INSERT INTO users (id, person_id, login_identifier, status) "
        "VALUES (:id, :person, :login, 'active')",
        id=user_id, person=person_id, login=f"m-{user_id.hex[:8]}@club.test",
    )
    _execute(
        "INSERT INTO notifications (id, event_type, subject_type, recipient_user_id, "
        "template_id, idempotency_key) VALUES (:id, 'event.cancelled', 'event', :user, "
        ":template, :key)",
        id=notification_id, user=user_id, template=template_id, key=f"k-{notification_id}",
    )
    return notification_id


# --- 1. Nothing existed --------------------------------------------------------------


@requires_postgres
def test_clean_upgrade_creates_and_downgrade_removes_only_the_seeded_rows(
    database_url: str,
) -> None:
    assert _templates() == _SEEDED_TEMPLATES
    assert _rules() == _SEEDED_RULES
    try:
        _alembic("downgrade", _PARENT, database_url=database_url)
        assert _templates() == set()
        assert _rules() == set()
    finally:
        _alembic("upgrade", "head", database_url=database_url)
    assert _templates() == _SEEDED_TEMPLATES
    assert _rules() == _SEEDED_RULES


@requires_postgres
def test_downgrade_keeps_unrelated_configuration(database_url: str) -> None:
    other_template = _existing_template("news_published.telegram", active=True)
    other_rule = _existing_rule("news.published", "news_audience", enabled=True)
    try:
        _alembic("downgrade", _PARENT, database_url=database_url)
        assert _templates() == {("news_published.telegram", other_template, True)}
        assert _rules() == {("news.published", "news_audience", other_rule, True)}
    finally:
        _alembic("upgrade", "head", database_url=database_url)


# --- 2-4. Rows existed before the upgrade --------------------------------------------


@requires_postgres
def test_preexisting_template_is_neither_overwritten_nor_deleted(database_url: str) -> None:
    try:
        _alembic("downgrade", _PARENT, database_url=database_url)
        own = _existing_template("event_cancelled.telegram", body="Своя формулировка")
        _alembic("upgrade", "head", database_url=database_url)
        assert _templates() == {
            ("event_cancelled.telegram", own, False),
            ("event_rescheduled.telegram", _RESCHEDULED_TEMPLATE_ID, True),
        }
        assert _template_bodies()["event_cancelled.telegram"] == "Своя формулировка"

        _alembic("downgrade", _PARENT, database_url=database_url)
        assert _templates() == {("event_cancelled.telegram", own, False)}
        assert _template_bodies()["event_cancelled.telegram"] == "Своя формулировка"
    finally:
        _alembic("upgrade", "head", database_url=database_url)


@requires_postgres
def test_preexisting_enabled_rule_stays_enabled_through_upgrade_and_downgrade(
    database_url: str,
) -> None:
    try:
        _alembic("downgrade", _PARENT, database_url=database_url)
        own = _existing_rule("event.cancelled", "registered_participants", enabled=True)
        _alembic("upgrade", "head", database_url=database_url)
        expected_after_upgrade = (
            _SEEDED_RULES
            - {("event.cancelled", "registered_participants",
                _RULE_IDS[("event.cancelled", "registered_participants")], False)}
        ) | {("event.cancelled", "registered_participants", own, True)}
        assert _rules() == expected_after_upgrade

        _alembic("downgrade", _PARENT, database_url=database_url)
        assert _rules() == {("event.cancelled", "registered_participants", own, True)}
    finally:
        _alembic("upgrade", "head", database_url=database_url)


@requires_postgres
def test_partially_preexisting_rows_keep_only_the_prior_ones_on_downgrade(
    database_url: str,
) -> None:
    try:
        _alembic("downgrade", _PARENT, database_url=database_url)
        own_template = _existing_template("event_rescheduled.telegram", active=True)
        own_rule = _existing_rule("event.cancelled", "telegram_destination", enabled=True)
        _alembic("upgrade", "head", database_url=database_url)
        assert len(_templates()) == 2 and len(_rules()) == 4
        assert ("event_rescheduled.telegram", own_template, True) in _templates()
        assert ("event.cancelled", "telegram_destination", own_rule, True) in _rules()

        _alembic("downgrade", _PARENT, database_url=database_url)
        assert _templates() == {("event_rescheduled.telegram", own_template, True)}
        assert _rules() == {("event.cancelled", "telegram_destination", own_rule, True)}
    finally:
        _alembic("upgrade", "head", database_url=database_url)


# --- 5. Notification history ------------------------------------------------------------


@requires_postgres
def test_downgrade_refuses_without_partial_deletes_while_a_seeded_template_is_used(
    database_url: str,
) -> None:
    notification_id = _notification_using(_CANCELLED_TEMPLATE_ID)
    with get_engine().connect() as connection:
        revision_before = connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        all_rows_before = connection.execute(
            sa.text(
                "SELECT (SELECT count(*) FROM notification_rules), "
                "(SELECT count(*) FROM notification_templates)"
            )
        ).one()
    try:
        result = run_alembic("downgrade", _PARENT, database_url=database_url)
        assert result.returncode != 0
        assert "downgrade refused" in result.stderr
        # Nothing was removed: every seeded rule and template is still there.
        assert _templates() == _SEEDED_TEMPLATES
        assert _rules() == _SEEDED_RULES
        with get_engine().connect() as connection:
            assert connection.execute(
                sa.text("SELECT count(*) FROM notifications WHERE id = :id"),
                {"id": notification_id},
            ).scalar_one() == 1
            # One transaction: neither this migration nor any later one it
            # passed through on the way down changed anything.
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == revision_before
            assert connection.execute(
                sa.text(
                    "SELECT (SELECT count(*) FROM notification_rules), "
                    "(SELECT count(*) FROM notification_templates)"
                )
            ).one() == all_rows_before
    finally:
        _alembic("upgrade", "head", database_url=database_url)


@requires_postgres
def test_history_on_a_preexisting_template_does_not_block_the_downgrade(
    database_url: str,
) -> None:
    try:
        _alembic("downgrade", _PARENT, database_url=database_url)
        own = _existing_template("event_cancelled.telegram", active=True)
        _alembic("upgrade", "head", database_url=database_url)
        _notification_using(own)
        _alembic("downgrade", _PARENT, database_url=database_url)
        assert _templates() == {("event_cancelled.telegram", own, True)}
    finally:
        _alembic("upgrade", "head", database_url=database_url)


# --- 6. Re-running the upgrade ---------------------------------------------------------


@requires_postgres
def test_rerunning_the_upgrade_neither_duplicates_nor_overwrites(database_url: str) -> None:
    # An administrator's changes to the seeded rows after the first upgrade.
    _execute("UPDATE notification_rules SET is_enabled = true")
    _execute(
        "UPDATE notification_templates SET body_template = 'Изменено', is_active = false "
        "WHERE id = :id",
        id=_RESCHEDULED_TEMPLATE_ID,
    )
    before_rules, before_templates = _rules(), _templates()
    try:
        # Re-run the upgrade's statements over the existing rows.
        _alembic("stamp", _PARENT, database_url=database_url)
        _alembic("upgrade", "head", database_url=database_url)
        assert _rules() == before_rules
        assert _templates() == before_templates
        assert all(enabled for *_rest, enabled in _rules())
        assert _template_bodies()["event_rescheduled.telegram"] == "Изменено"
    finally:
        _alembic("upgrade", "head", database_url=database_url)
