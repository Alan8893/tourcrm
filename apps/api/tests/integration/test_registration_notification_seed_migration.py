"""Migration 700a5f703346 (Issue #336 PR-2): seeding the registration
templates and rules never takes over, overwrites or deletes configuration
it did not create.

Real `alembic upgrade`/`downgrade`/`stamp` runs against PostgreSQL through
the shared `run_alembic` helper, like the PR-1 seed migration tests. Only
the registration rows are compared; rows of other migrations are not this
migration's concern. Every test leaves the schema at head; the
integration fixtures restore the data baseline before the next test.
"""

import uuid
from typing import Any

import sqlalchemy as sa

from app.db.session import get_engine

from ._schema_reset import run_alembic
from .conftest import requires_postgres

_PARENT = "a65b609f4d34"

# The migration's frozen primary keys.
_CREATED_TEMPLATE_ID = uuid.UUID("6ce95ca6-150d-4f9b-9c74-8a0f8abc5fd7")
_CANCELLED_TEMPLATE_ID = uuid.UUID("47d50261-b5a7-43e8-be8c-a870220d69bb")
_CREATED_RULE_ID = uuid.UUID("c1f92c00-53b5-417f-ba2a-2b1f8f943408")
_CANCELLED_RULE_ID = uuid.UUID("33d73e97-1f6a-41f0-abac-13a398e2c6ff")

_CODES = ("registration_created.telegram", "registration_cancelled.telegram")
_EVENT_TYPES = ("registration.created", "registration.cancelled")
_SEEDED_TEMPLATES = {
    ("registration_created.telegram", _CREATED_TEMPLATE_ID, True),
    ("registration_cancelled.telegram", _CANCELLED_TEMPLATE_ID, True),
}
_SEEDED_RULES = {
    ("registration.created", "registrant", _CREATED_RULE_ID, False),
    ("registration.cancelled", "registrant", _CANCELLED_RULE_ID, False),
}


def _alembic(*args: str, database_url: str) -> None:
    result = run_alembic(*args, database_url=database_url)
    assert result.returncode == 0, result.stderr


def _templates() -> set[tuple[str, uuid.UUID, bool]]:
    with get_engine().connect() as connection:
        return {
            (row.code, row.id, row.is_active)
            for row in connection.execute(
                sa.text("SELECT code, id, is_active FROM notification_templates")
            )
            if row.code in _CODES
        }


def _template_body(code: str) -> str:
    with get_engine().connect() as connection:
        return connection.execute(
            sa.text("SELECT body_template FROM notification_templates WHERE code = :code"),
            {"code": code},
        ).scalar_one()


def _rules() -> set[tuple[str, str, uuid.UUID, bool]]:
    with get_engine().connect() as connection:
        return {
            (row.event_type, row.recipient_scope, row.id, row.is_enabled)
            for row in connection.execute(
                sa.text(
                    "SELECT event_type, recipient_scope, id, is_enabled FROM notification_rules"
                )
            )
            if row.event_type in _EVENT_TYPES
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


def _existing_rule(event_type: str, *, enabled: bool = True) -> uuid.UUID:
    rule_id = uuid.uuid4()
    _execute(
        "INSERT INTO notification_rules (id, club_id, event_type, channel, recipient_scope, "
        "is_enabled) VALUES (:id, NULL, :event_type, 'telegram', 'registrant', :enabled)",
        id=rule_id, event_type=event_type, enabled=enabled,
    )
    return rule_id


def _notification_using(template_id: uuid.UUID) -> None:
    user_id, person_id, notification_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _execute(
        "INSERT INTO persons (id, last_name, first_name) VALUES (:id, 'R', 'T')", id=person_id
    )
    _execute(
        "INSERT INTO users (id, person_id, login_identifier, status) "
        "VALUES (:id, :person, :login, 'active')",
        id=user_id, person=person_id, login=f"r-{user_id.hex[:8]}@club.test",
    )
    _execute(
        "INSERT INTO notifications (id, event_type, subject_type, recipient_user_id, "
        "template_id, idempotency_key) VALUES (:id, 'registration.created', 'event', :user, "
        ":template, :key)",
        id=notification_id, user=user_id, template=template_id, key=f"k-{notification_id}",
    )


def _revision_and_counts() -> tuple[Any, ...]:
    with get_engine().connect() as connection:
        return tuple(
            connection.execute(
                sa.text(
                    "SELECT (SELECT version_num FROM alembic_version), "
                    "(SELECT count(*) FROM notification_rules), "
                    "(SELECT count(*) FROM notification_templates)"
                )
            ).one()
        )


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
def test_preexisting_template_is_neither_overwritten_nor_deleted(database_url: str) -> None:
    try:
        _alembic("downgrade", _PARENT, database_url=database_url)
        own = _existing_template("registration_cancelled.telegram")
        _alembic("upgrade", "head", database_url=database_url)
        assert _templates() == {
            ("registration_created.telegram", _CREATED_TEMPLATE_ID, True),
            ("registration_cancelled.telegram", own, False),
        }
        assert _template_body("registration_cancelled.telegram") == "Своя формулировка"
        _alembic("downgrade", _PARENT, database_url=database_url)
        assert _templates() == {("registration_cancelled.telegram", own, False)}
    finally:
        _alembic("upgrade", "head", database_url=database_url)


@requires_postgres
def test_preexisting_enabled_rule_stays_enabled(database_url: str) -> None:
    try:
        _alembic("downgrade", _PARENT, database_url=database_url)
        own = _existing_rule("registration.created", enabled=True)
        _alembic("upgrade", "head", database_url=database_url)
        assert _rules() == {
            ("registration.created", "registrant", own, True),
            ("registration.cancelled", "registrant", _CANCELLED_RULE_ID, False),
        }
        _alembic("downgrade", _PARENT, database_url=database_url)
        assert _rules() == {("registration.created", "registrant", own, True)}
    finally:
        _alembic("upgrade", "head", database_url=database_url)


@requires_postgres
def test_downgrade_refuses_without_partial_deletes_while_a_seeded_template_is_used(
    database_url: str,
) -> None:
    _notification_using(_CREATED_TEMPLATE_ID)
    before = _revision_and_counts()
    try:
        result = run_alembic("downgrade", _PARENT, database_url=database_url)
        assert result.returncode != 0
        assert "downgrade refused" in result.stderr
        assert _revision_and_counts() == before
        assert _templates() == _SEEDED_TEMPLATES
        assert _rules() == _SEEDED_RULES
    finally:
        _alembic("upgrade", "head", database_url=database_url)


@requires_postgres
def test_history_on_a_preexisting_template_does_not_block_the_downgrade(
    database_url: str,
) -> None:
    try:
        _alembic("downgrade", _PARENT, database_url=database_url)
        own = _existing_template("registration_created.telegram", active=True)
        _alembic("upgrade", "head", database_url=database_url)
        _notification_using(own)
        _alembic("downgrade", _PARENT, database_url=database_url)
        assert _templates() == {("registration_created.telegram", own, True)}
    finally:
        _alembic("upgrade", "head", database_url=database_url)


@requires_postgres
def test_rerunning_the_upgrade_neither_duplicates_nor_overwrites(database_url: str) -> None:
    _execute(
        "UPDATE notification_rules SET is_enabled = true WHERE id = :id", id=_CREATED_RULE_ID
    )
    _execute(
        "UPDATE notification_templates SET body_template = 'Изменено' WHERE id = :id",
        id=_CANCELLED_TEMPLATE_ID,
    )
    before_rules, before_templates = _rules(), _templates()
    try:
        _alembic("stamp", _PARENT, database_url=database_url)
        _alembic("upgrade", "head", database_url=database_url)
        assert _rules() == before_rules
        assert _templates() == before_templates
        assert ("registration.created", "registrant", _CREATED_RULE_ID, True) in _rules()
        assert _template_body("registration_cancelled.telegram") == "Изменено"
    finally:
        _alembic("upgrade", "head", database_url=database_url)
