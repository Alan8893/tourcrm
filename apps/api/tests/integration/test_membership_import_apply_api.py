"""Integration tests for participant import approve/apply/report
(TH-0118.3 / Issue #193; docs/05-api/people-api.md §22):

    POST /api/v1/memberships/imports/{import_id}/approve
    POST /api/v1/memberships/imports/{import_id}/apply
    GET  /api/v1/memberships/imports/{import_id}            (aggregate report)
    GET  /api/v1/memberships/imports/{import_id}/errors     (row-level report)

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring tests/integration/test_membership_import_preview_api.py's
fixture conventions (local, duplicated helpers; `get_file_storage`
overridden to a `LocalFileStorage` rooted at `tmp_path`). Jobs are uploaded,
previewed and approved through the real endpoints, so apply re-reads the
source file through the real FileStorage path.
"""

import datetime
import io
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select, text

import app.imports.apply as apply_module
import app.people.service as people_service_module
from app.api.deps import CurrentPrincipal, get_current_principal
from app.audit.security import assert_safe_audit_details
from app.authentication import account_provisioning
from app.db.attendance import Attendance
from app.db.audit import AuditLog
from app.db.authentication import PasswordResetChallenge
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.documents import File
from app.db.events import EventParticipation
from app.db.groups import GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.imports import ImportJob
from app.db.session import session_scope
from app.imports.apply import (
    ImportJobStatusConflictError,
    approve_import_job,
    run_import_apply,
)
from app.main import app
from app.storage.local import LocalFileStorage, get_file_storage

from .conftest import requires_postgres

pytestmark = requires_postgres

_URL = "/api/v1/memberships/imports"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_HEADER = "first_name,last_name,middle_name,birth_date,phone,email,external_id"


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def client(storage_root: Path) -> TestClient:
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(root=storage_root)
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


# --- fixtures / factories ---------------------------------------------------


def _make_club() -> uuid.UUID:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.commit()
        return club.id


def _make_person(**values) -> uuid.UUID:
    defaults = {"last_name": "Existing", "first_name": f"P-{uuid.uuid4().hex[:8]}"}
    defaults.update(values)
    with session_scope() as session:
        person = Person(**defaults)
        session.add(person)
        session.commit()
        return person.id


def _make_user(*, login: str | None = None) -> uuid.UUID:
    with session_scope() as session:
        person = Person(last_name="Importer", first_name=f"U-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person,
            login_identifier=login or f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add_all([person, user])
        session.commit()
        return user.id


def _grant(user_id: uuid.UUID, *, club_id: uuid.UUID | None, scope_type: str = "all") -> None:
    with session_scope() as session:
        permission = session.execute(
            select(Permission).where(Permission.code == "membership.import")
        ).scalar_one()
        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
        session.add(role)
        session.flush()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=role.id, scope_type=scope_type, club_id=club_id
            )
        )
        session.commit()


def _importer(club_id: uuid.UUID) -> uuid.UUID:
    user_id = _make_user()
    _grant(user_id, club_id=club_id)
    return user_id


def _admin(club_id: uuid.UUID | None) -> uuid.UUID:
    user_id = _make_user()
    with session_scope() as session:
        admin_role = session.execute(select(Role).where(Role.code == "admin")).scalar_one()
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=admin_role.id, scope_type="all", club_id=club_id
            )
        )
        session.commit()
    return user_id


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def _xlsx(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _upload(client: TestClient, content: bytes, *, filename: str = "members.csv") -> str:
    mime_type = _XLSX_MIME if filename.endswith(".xlsx") else "text/csv"
    response = client.post(
        _URL, files={"file": (filename, content, mime_type)}, headers=_csrf_headers(client)
    )
    assert response.status_code == 201, response.text
    return response.json()["import_id"]


def _post(client: TestClient, import_id: str | uuid.UUID, action: str):
    return client.post(f"{_URL}/{import_id}/{action}", headers=_csrf_headers(client))


def _preview(client: TestClient, import_id: str) -> None:
    response = _post(client, import_id, "preview")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "preview_ready"


def _approve(client: TestClient, import_id: str) -> None:
    response = _post(client, import_id, "approve")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"


def _approved_job(client: TestClient, content: bytes, *, filename: str = "members.csv") -> str:
    import_id = _upload(client, content, filename=filename)
    _preview(client, import_id)
    _approve(client, import_id)
    return import_id


def _errors(client: TestClient, import_id: str, **params) -> list[dict]:
    response = client.get(f"{_URL}/{import_id}/errors", params={"page_size": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _setup(client: TestClient) -> tuple[uuid.UUID, uuid.UUID]:
    club_id = _make_club()
    user_id = _importer(club_id)
    _authenticate_as(user_id)
    return club_id, user_id


def _status(import_id: str | uuid.UUID) -> str:
    with session_scope() as session:
        job = session.get(ImportJob, uuid.UUID(str(import_id)))
        assert job is not None
        return job.status


def _count(model, *conditions) -> int:
    with session_scope() as session:
        return session.execute(
            select(func.count()).select_from(model).where(*conditions)
        ).scalar_one()


_DOMAIN_MODELS = (
    Person,
    User,
    ClubMembership,
    UserRoleAssignment,
    GroupMembership,
    GroupInstructorAssignment,
    GuardianRelationship,
    EventParticipation,
    Attendance,
    PasswordResetChallenge,
)


def _domain_counts() -> dict[str, int]:
    return {model.__name__: _count(model) for model in _DOMAIN_MODELS}


def _audit(action: str, **conditions) -> list[AuditLog]:
    with session_scope() as session:
        query = select(AuditLog).where(AuditLog.action == action)
        for column, value in conditions.items():
            query = query.where(getattr(AuditLog, column) == value)
        rows = list(session.execute(query).scalars())
        for row in rows:
            session.expunge(row)
        return rows


def _person_by_first_name(first_name: str) -> Person | None:
    with session_scope() as session:
        person = session.execute(
            select(Person).where(Person.first_name == first_name)
        ).scalar_one_or_none()
        if person is not None:
            session.expunge(person)
        return person


def _user_of(person_id: uuid.UUID) -> User | None:
    with session_scope() as session:
        user = session.execute(select(User).where(User.person_id == person_id)).scalar_one_or_none()
        if user is not None:
            session.expunge(user)
        return user


def _memberships_of(person_id: uuid.UUID) -> list[ClubMembership]:
    with session_scope() as session:
        rows = list(
            session.execute(
                select(ClubMembership).where(ClubMembership.person_id == person_id)
            ).scalars()
        )
        for row in rows:
            session.expunge(row)
        return rows


def _person_snapshot(person_id: uuid.UUID) -> dict:
    with session_scope() as session:
        person = session.get(Person, person_id)
        assert person is not None
        user = session.execute(select(User).where(User.person_id == person_id)).scalar_one_or_none()
        return {
            "person": {
                column.name: getattr(person, column.key) for column in Person.__table__.columns
            },
            "user": None
            if user is None
            else {column.name: getattr(user, column.key) for column in User.__table__.columns},
            "memberships": _count(ClubMembership, ClubMembership.person_id == person_id),
        }


# --- approve ----------------------------------------------------------------


def test_approve_moves_preview_ready_to_approved_without_domain_mutation(
    client: TestClient,
) -> None:
    _setup(client)
    import_id = _upload(client, _csv(_HEADER, "Anna,Ivanova,,2010-05-01,,anna@example.com,"))
    _preview(client, import_id)
    before = _domain_counts()
    audit_before = _count(AuditLog)

    response = _post(client, import_id, "approve")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["import_id"] == import_id
    assert body["statistics"]["created_records"] is None
    assert body["statistics"]["updated_records"] is None
    assert body["statistics"]["skipped_records"] is None
    assert _status(import_id) == "approved"
    assert _domain_counts() == before
    assert _count(AuditLog) == audit_before


@pytest.mark.parametrize(
    "job_status",
    [
        "uploaded",
        "parsing",
        "validating",
        "approved",
        "applying",
        "completed",
        "partially_completed",
        "failed",
        "cancelled",
    ],
)
def test_approve_from_any_other_status_is_409_and_changes_nothing(
    client: TestClient, job_status: str
) -> None:
    club_id, user_id = _setup(client)
    job_id = _insert_job(club_id=club_id, created_by_user_id=user_id, status=job_status)

    response = _post(client, job_id, "approve")

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_import_job_status_transition"
    assert error["details"]["status"] == job_status
    assert _status(job_id) == job_status


def test_second_approve_is_409(client: TestClient) -> None:
    _setup(client)
    import_id = _approved_job(client, _csv(_HEADER, "Anna,Ivanova,,,,,"))

    response = _post(client, import_id, "approve")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_import_job_status_transition"
    assert _status(import_id) == "approved"


# --- apply: lifecycle guard ---------------------------------------------------


@pytest.mark.parametrize(
    "job_status",
    [
        "uploaded",
        "parsing",
        "validating",
        "preview_ready",
        "applying",
        "completed",
        "partially_completed",
        "failed",
        "cancelled",
    ],
)
def test_apply_from_any_status_but_approved_is_409_and_changes_nothing(
    client: TestClient, job_status: str
) -> None:
    club_id, user_id = _setup(client)
    job_id = _insert_job(club_id=club_id, created_by_user_id=user_id, status=job_status)
    before = _domain_counts()

    response = _post(client, job_id, "apply")

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_import_job_status_transition"
    assert error["details"]["status"] == job_status
    assert _status(job_id) == job_status
    assert _domain_counts() == before
    assert _audit("membership.import.applied") == []


def test_apply_of_a_previewed_but_unapproved_job_is_409(client: TestClient) -> None:
    _setup(client)
    import_id = _upload(client, _csv(_HEADER, "Anna,Ivanova,,,,,"))
    _preview(client, import_id)
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 409
    assert _status(import_id) == "preview_ready"
    assert _domain_counts() == before


# --- apply: successful creation ------------------------------------------------


def test_successful_csv_apply_creates_person_user_and_membership_only(
    client: TestClient,
) -> None:
    club_id, importer_id = _setup(client)
    import_id = _approved_job(
        client,
        _csv(
            _HEADER,
            "Anna,Ivanova,Petrovna,2010-05-01,+7 900 111,  Anna.Ivanova@Example.COM ,E1",
            "Boris,Petrov,,,,,",
        ),
    )
    before = _domain_counts()
    started = datetime.datetime.now(datetime.timezone.utc)

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["statistics"]["created_records"] == 2
    assert body["statistics"]["updated_records"] == 0
    assert body["statistics"]["skipped_records"] == 0
    assert body["statistics"]["total_records"] == 2
    assert "temporary_credential" not in response.text

    after = _domain_counts()
    assert after["Person"] == before["Person"] + 2
    assert after["User"] == before["User"] + 2
    assert after["ClubMembership"] == before["ClubMembership"] + 2
    # Out of TH-0118.3's creation scope.
    for name in (
        "UserRoleAssignment",
        "GroupMembership",
        "GroupInstructorAssignment",
        "GuardianRelationship",
        "EventParticipation",
        "Attendance",
    ):
        assert after[name] == before[name], name

    anna = _person_by_first_name("Anna")
    assert anna is not None
    assert (anna.last_name, anna.middle_name) == ("Ivanova", "Petrovna")
    assert anna.birth_date == datetime.date(2010, 5, 1)
    assert anna.phone == "+7 900 111"
    assert anna.email == "anna.ivanova@example.com"

    for person in (anna, _person_by_first_name("Boris")):
        assert person is not None
        memberships = _memberships_of(person.id)
        assert len(memberships) == 1
        membership = memberships[0]
        assert membership.club_id == club_id
        assert membership.membership_type == "member"
        assert membership.status == "active"
        assert membership.left_at is None
        assert membership.joined_at >= started - datetime.timedelta(seconds=5)
        assert _user_of(person.id) is not None

    assert importer_id is not None


def test_successful_xlsx_apply(client: TestClient) -> None:
    _setup(client)
    import_id = _approved_job(
        client,
        _xlsx(
            [
                ["first_name", "last_name", "birth_date", "phone", "email"],
                ["Xenia", "Orlova", datetime.date(2011, 2, 3), 79001112233, "xenia@example.com"],
                ["Yuri", "Sokolov", None, None, None],
            ]
        ),
        filename="members.xlsx",
    )

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.json()["statistics"]["created_records"] == 2
    xenia = _person_by_first_name("Xenia")
    assert xenia is not None
    assert xenia.birth_date == datetime.date(2011, 2, 3)
    assert xenia.phone == "79001112233"
    user = _user_of(xenia.id)
    assert user is not None and user.login_identifier == "xenia@example.com"
    yuri = _person_by_first_name("Yuri")
    assert yuri is not None and _user_of(yuri.id) is not None
    assert len(_memberships_of(yuri.id)) == 1


# --- apply: account provisioning --------------------------------------------------


def test_row_with_email_creates_active_user_without_first_access_credential(
    client: TestClient,
) -> None:
    club_id, _ = _setup(client)
    import_id = _approved_job(client, _csv(_HEADER, "Anna,Ivanova,,,,ANNA@Example.com,"))

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    anna = _person_by_first_name("Anna")
    assert anna is not None
    user = _user_of(anna.id)
    assert user is not None
    assert user.status == "active"
    assert user.login_identifier == "anna@example.com"
    assert user.normalized_login_identifier == "anna@example.com"
    assert user.password_hash is None
    # D2: import creates the account only — no first-access challenge, no
    # `password_reset_challenge.created` audit, nothing to leak.
    assert _count(PasswordResetChallenge) == 0
    assert _audit("password_reset_challenge.created") == []
    for body in (
        response.text,
        client.get(f"{_URL}/{import_id}").text,
        str(_errors(client, import_id)),
    ):
        assert "credential" not in body and "token" not in body and "password" not in body

    # First access is issued later through the existing canonical
    # administrator password-reset flow.
    _authenticate_as(_admin(club_id))
    reset = client.post(
        f"/api/v1/persons/{anna.id}/account/password-reset", headers=_csrf_headers(client)
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["temporary_credential"]
    assert _count(PasswordResetChallenge, PasswordResetChallenge.user_id == user.id) == 1


def test_row_without_email_creates_pending_stub_user(client: TestClient) -> None:
    _setup(client)
    import_id = _approved_job(client, _csv(_HEADER, "Boris,Petrov,,,+7 900 222,,"))
    users_before = _count(User)

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    boris = _person_by_first_name("Boris")
    assert boris is not None
    assert boris.email is None
    user = _user_of(boris.id)
    # Never a Person-only fallback: the User exists as a pending stub.
    assert user is not None
    assert user.status == "pending"
    assert user.login_identifier is None
    assert user.normalized_login_identifier is None
    assert user.password_hash is None
    assert _count(PasswordResetChallenge, PasswordResetChallenge.user_id == user.id) == 0
    assert _count(User) == users_before + 1
    # No fake login derived from phone/person id/anything else.
    assert _count(User, User.login_identifier.contains("+7 900 222")) == 0
    assert _count(User, User.login_identifier == str(boris.id)) == 0


# --- apply: duplicates -------------------------------------------------------------


@pytest.mark.parametrize(
    ("existing", "row"),
    [
        ({"email": "Dup@Example.com"}, "Anna,Ivanova,,,,dup@example.com,"),
        ({"phone": "+7 900 555"}, "Anna,Ivanova,,,+7 900 555,,"),
        (
            {"first_name": "Anna", "last_name": "Ivanova", "birth_date": datetime.date(2010, 5, 1)},
            "Anna,Ivanova,,2010-05-01,,,",
        ),
    ],
    ids=["email", "phone", "name_birth_date"],
)
def test_duplicate_of_existing_person_is_skipped_and_existing_is_untouched(
    client: TestClient, existing: dict, row: str
) -> None:
    _setup(client)
    person_id = _make_person(**existing)
    snapshot = _person_snapshot(person_id)
    import_id = _approved_job(client, _csv(_HEADER, row, "Boris,Petrov,,,,,"))
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["statistics"]["created_records"] == 1
    assert body["statistics"]["updated_records"] == 0
    assert body["statistics"]["skipped_records"] == 1
    assert _count(Person) == before["Person"] + 1
    assert _count(User) == before["User"] + 1
    # No merge / update / overwrite / reuse: the matched Person is exactly
    # as it was, and got no User or membership from the import.
    assert _person_snapshot(person_id) == snapshot


def test_duplicate_of_existing_user_login_is_skipped_and_user_is_untouched(
    client: TestClient,
) -> None:
    _setup(client)
    existing_user_id = _make_user(login="taken@example.com")
    with session_scope() as session:
        existing_person_id = session.get(User, existing_user_id).person_id
    snapshot = _person_snapshot(existing_person_id)
    import_id = _approved_job(client, _csv(_HEADER, "Anna,Ivanova,,,,taken@example.com,"))
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["statistics"]["created_records"] == 0
    assert body["statistics"]["skipped_records"] == 1
    assert _domain_counts() == before
    assert _person_snapshot(existing_person_id) == snapshot


def test_duplicate_created_after_preview_is_detected_at_apply_and_skipped(
    client: TestClient,
) -> None:
    _setup(client)
    import_id = _approved_job(
        client, _csv(_HEADER, "Anna,Ivanova,,,+7 900 777,,", "Boris,Petrov,,,,,")
    )
    assert [e for e in _errors(client, import_id) if e["code"] == "duplicate_exact"] == []
    # Someone creates the same person between preview/approve and apply.
    late_person_id = _make_person(first_name="Late", phone="+7 900 777")
    snapshot = _person_snapshot(late_person_id)
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["statistics"]["created_records"] == 1
    assert body["statistics"]["skipped_records"] == 1
    assert _count(Person) == before["Person"] + 1
    assert _person_by_first_name("Anna") is None
    assert _person_snapshot(late_person_id) == snapshot
    # The row-level report explains the skip.
    warnings = [e for e in _errors(client, import_id) if e["code"] == "duplicate_exact"]
    assert len(warnings) == 1
    assert warnings[0]["row_number"] == 2
    assert warnings[0]["field"] == "phone"
    assert warnings[0]["severity"] == "warning"
    assert warnings[0]["matched_person_id"] == str(late_person_id)


def test_duplicate_rows_inside_the_file_are_all_skipped(client: TestClient) -> None:
    _setup(client)
    import_id = _approved_job(
        client,
        _csv(
            _HEADER,
            "Anna,Ivanova,,,,same@example.com,",
            "Boris,Petrov,,,,,",
            "Anya,Ivanova,,,,same@example.com,",
        ),
    )
    errors_before = _errors(client, import_id)

    response = _post(client, import_id, "apply")

    body = response.json()
    assert body["status"] == "completed"
    assert body["statistics"]["created_records"] == 1
    assert body["statistics"]["skipped_records"] == 2
    assert _person_by_first_name("Anna") is None
    assert _person_by_first_name("Anya") is None
    assert _person_by_first_name("Boris") is not None
    # Preview's entries are kept as they were; nothing is duplicated.
    assert _errors(client, import_id) == errors_before


# --- apply: invalid rows -------------------------------------------------------------


def test_invalid_rows_are_skipped_and_create_nothing(client: TestClient) -> None:
    _setup(client)
    import_id = _approved_job(
        client,
        _csv(
            _HEADER,
            "Anna,,,,,,",  # last_name missing
            "Boris,Petrov,,,,not-an-email,",
            "Vera,Ivanova,,05/01/2010,,,",  # invalid birth_date
            "Gleb,Orlov,,,,,",
        ),
    )
    errors_before = _errors(client, import_id)
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["statistics"]["created_records"] == 1
    assert body["statistics"]["updated_records"] == 0
    assert body["statistics"]["skipped_records"] == 3
    assert body["statistics"]["invalid_records"] == 3
    assert _count(Person) == before["Person"] + 1
    assert _count(User) == before["User"] + 1
    assert _count(ClubMembership) == before["ClubMembership"] + 1
    for first_name in ("Anna", "Boris", "Vera"):
        assert _person_by_first_name(first_name) is None
    # Validation errors stay available, unchanged.
    assert _errors(client, import_id) == errors_before
    assert body["statistics"]["error_count"] == len(
        [e for e in errors_before if e["severity"] == "error"]
    )


def test_all_rows_skipped_is_still_completed(client: TestClient) -> None:
    _setup(client)
    _make_person(phone="+7 900 999")
    import_id = _approved_job(client, _csv(_HEADER, "Anna,,,,,,", "Boris,Petrov,,,+7 900 999,,"))
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    body = response.json()
    assert body["status"] == "completed"
    assert body["statistics"]["created_records"] == 0
    assert body["statistics"]["updated_records"] == 0
    assert body["statistics"]["skipped_records"] == 2
    assert _domain_counts() == before
    [applied] = _audit("membership.import.applied")
    assert applied.outcome == "success"


# --- apply: row transactions / final statuses ----------------------------------------


@pytest.fixture
def failing_rows(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """First names whose User creation fails *after* the Person, its
    ClubMembership, its User and their audit rows were already flushed in
    the row transaction — so a rollback of the whole row is observable."""
    failing: set[str] = set()
    original = account_provisioning.create_user_for_person

    def create_user_for_person(session, *, person_id, **kwargs):
        result = original(session, person_id=person_id, **kwargs)
        person = session.get(Person, person_id)
        if person is not None and person.first_name in failing:
            raise RuntimeError("simulated row failure")
        return result

    monkeypatch.setattr(account_provisioning, "create_user_for_person", create_user_for_person)
    return failing


def test_failed_row_is_rolled_back_and_others_stay_committed(
    client: TestClient, failing_rows: set[str]
) -> None:
    _setup(client)
    failing_rows.add("Boom")
    import_id = _approved_job(
        client,
        _csv(
            _HEADER,
            "Anna,Ivanova,,,,anna@example.com,",
            "Boom,Failing,,,,boom@example.com,",
            "Vera,Petrova,,,,,",
            "Gleb,,,,,,",  # invalid
        ),
    )
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "partially_completed"
    assert body["statistics"]["created_records"] == 2
    assert body["statistics"]["updated_records"] == 0
    assert body["statistics"]["skipped_records"] == 1
    # The failed row left nothing behind: no Person, User, membership,
    # challenge or domain audit record.
    assert _person_by_first_name("Boom") is None
    assert _count(User, User.login_identifier == "boom@example.com") == 0
    after = _domain_counts()
    assert after["Person"] == before["Person"] + 2
    assert after["User"] == before["User"] + 2
    assert after["ClubMembership"] == before["ClubMembership"] + 2
    assert after["PasswordResetChallenge"] == before["PasswordResetChallenge"]
    assert len(_audit("person.created")) == 2
    assert len(_audit("user.created")) == 2
    assert len(_audit("membership.created")) == 2
    # Successful rows before and after the failure are committed.
    assert _person_by_first_name("Anna") is not None
    assert _person_by_first_name("Vera") is not None
    # The failure is recorded in the row-level report.
    [failure] = [e for e in _errors(client, import_id) if e["code"] == "import_apply_failed"]
    assert failure["row_number"] == 3
    assert failure["severity"] == "error"
    assert "boom" not in failure["message"].lower()
    [applied] = _audit("membership.import.applied")
    assert applied.outcome == "success"
    assert applied.details["status"] == "partially_completed"
    assert applied.details["failed_records"] == 1


def test_every_candidate_failing_makes_the_job_failed(
    client: TestClient, failing_rows: set[str]
) -> None:
    _setup(client)
    failing_rows.update({"Boom", "Bang"})
    import_id = _approved_job(
        client, _csv(_HEADER, "Boom,One,,,,,", "Bang,Two,,,,,", "Gleb,,,,,,")
    )
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["statistics"]["created_records"] == 0
    assert body["statistics"]["updated_records"] == 0
    assert body["statistics"]["skipped_records"] == 1
    assert _domain_counts() == before
    [applied] = _audit("membership.import.applied")
    assert applied.outcome == "failure"
    assert applied.details["status"] == "failed"
    assert len([e for e in _errors(client, import_id) if e["code"] == "import_apply_failed"]) == 2


def test_unreadable_source_file_at_apply_makes_the_job_failed(
    client: TestClient, storage_root: Path
) -> None:
    _setup(client)
    import_id = _approved_job(client, _csv(_HEADER, "Anna,Ivanova,,,,,"))
    with session_scope() as session:
        job = session.get(ImportJob, uuid.UUID(import_id))
        storage_key = session.get(File, job.source_file_id).storage_key
    LocalFileStorage(root=storage_root).delete(storage_key)
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["statistics"]["created_records"] == 0
    assert body["statistics"]["updated_records"] == 0
    assert _domain_counts() == before
    codes = [e["code"] for e in _errors(client, import_id) if e["row_number"] is None]
    assert codes == ["import_file_unreadable"]
    [applied] = _audit("membership.import.applied")
    assert applied.outcome == "failure"


# --- apply: audit --------------------------------------------------------------------


def test_apply_audits_every_created_entity_and_the_batch_exactly_once(
    client: TestClient,
) -> None:
    club_id, importer_id = _setup(client)
    import_id = _approved_job(
        client,
        _csv(_HEADER, "Anna,Ivanova,,,+7 900 1,anna@example.com,", "Boris,Petrov,,,,,", "X,,,,,,"),
    )
    audit_before = _count(AuditLog)

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    anna = _person_by_first_name("Anna")
    boris = _person_by_first_name("Boris")
    assert anna is not None and boris is not None
    for person in (anna, boris):
        [person_created] = _audit("person.created", resource_id=person.id)
        assert person_created.actor_user_id == importer_id
        [membership] = _memberships_of(person.id)
        [membership_created] = _audit("membership.created", resource_id=membership.id)
        assert membership_created.club_id == club_id
        user = _user_of(person.id)
        assert user is not None
        assert len(_audit("user.created", resource_id=user.id)) == 1
    # No first-access challenge is issued by import, even for the email row.
    assert _audit("password_reset_challenge.created") == []

    [applied] = _audit("membership.import.applied")
    assert applied.resource_type == "import_job"
    assert applied.resource_id == uuid.UUID(import_id)
    assert applied.actor_type == "user"
    assert applied.actor_user_id == importer_id
    assert applied.club_id == club_id
    assert applied.outcome == "success"
    assert applied.details == {
        "status": "completed",
        "created_records": 2,
        "updated_records": 0,
        "skipped_records": 1,
        "failed_records": 0,
    }
    # 2 x (person, membership, user) + 1 batch record.
    assert _count(AuditLog) == audit_before + 7

    with session_scope() as session:
        for row in session.execute(select(AuditLog)).scalars():
            assert_safe_audit_details(row.details)
            serialized = str(row.details or {})
            assert "anna@example.com" not in serialized
            assert "+7 900 1" not in serialized


def test_second_apply_is_409_and_writes_nothing(client: TestClient) -> None:
    _setup(client)
    import_id = _approved_job(client, _csv(_HEADER, "Anna,Ivanova,,,,,"))
    assert _post(client, import_id, "apply").status_code == 200
    before = _domain_counts()
    audit_before = _count(AuditLog)

    response = _post(client, import_id, "apply")

    assert response.status_code == 409
    assert response.json()["error"]["details"]["status"] == "completed"
    assert _domain_counts() == before
    assert _count(AuditLog) == audit_before
    assert len(_audit("membership.import.applied")) == 1


# --- report ---------------------------------------------------------------------------


def test_report_endpoints_show_final_state_after_apply(client: TestClient) -> None:
    _setup(client)
    _make_person(email="dup@example.com")
    import_id = _approved_job(
        client,
        _csv(
            _HEADER,
            "Anna,Ivanova,,,,anna@example.com,",
            "Boris,,,,,,",
            "C,D,,,,dup@example.com,",
        ),
    )
    applied = _post(client, import_id, "apply").json()

    report = client.get(f"{_URL}/{import_id}")

    assert report.status_code == 200
    assert report.json() == applied
    statistics = report.json()["statistics"]
    assert report.json()["status"] == "completed"
    assert statistics["created_records"] == 1
    assert statistics["updated_records"] == 0
    assert statistics["skipped_records"] == 2
    assert statistics["total_records"] == 3
    items = _errors(client, import_id)
    assert {(e["row_number"], e["code"]) for e in items} == {
        (3, "required_field_missing"),
        (4, "duplicate_exact"),
    }
    assert set(items[0]) == {
        "id",
        "row_number",
        "field",
        "code",
        "message",
        "severity",
        "matched_person_id",
        "created_at",
    }
    assert [e["code"] for e in _errors(client, import_id, severity="warning")] == [
        "duplicate_exact"
    ]


# --- authorization ---------------------------------------------------------------------


@pytest.mark.parametrize("action", ["approve", "apply"])
def test_requires_authentication(client: TestClient, action: str) -> None:
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_importer(club_id))

    assert _post(client, job_id, action).status_code == 401
    assert _status(job_id) == "uploaded"


@pytest.mark.parametrize("action", ["approve", "apply"])
def test_requires_csrf_token(client: TestClient, action: str) -> None:
    club_id, user_id = _setup(client)
    job_id = _insert_job(club_id=club_id, created_by_user_id=user_id, status="preview_ready")

    response = client.post(f"{_URL}/{job_id}/{action}")

    assert response.status_code == 403
    assert _status(job_id) == "preview_ready"


def _assert_hidden(client: TestClient, job_id: uuid.UUID, action: str) -> None:
    hidden = _post(client, job_id, action)
    missing = _post(client, uuid.uuid4(), action)
    assert hidden.status_code == 404, hidden.text
    assert missing.status_code == 404
    assert hidden.json()["error"]["code"] == missing.json()["error"]["code"]
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


def _job_ready_for(action: str, *, club_id: uuid.UUID, creator: uuid.UUID) -> uuid.UUID:
    return _insert_job(
        club_id=club_id,
        created_by_user_id=creator,
        status="preview_ready" if action == "approve" else "approved",
    )


@pytest.mark.parametrize("action", ["approve", "apply"])
def test_other_importer_gets_404(client: TestClient, action: str) -> None:
    club_id = _make_club()
    job_id = _job_ready_for(action, club_id=club_id, creator=_importer(club_id))
    _authenticate_as(_importer(club_id))

    _assert_hidden(client, job_id, action)
    assert _status(job_id) in {"preview_ready", "approved"}


@pytest.mark.parametrize("action", ["approve", "apply"])
def test_user_without_permission_gets_404(client: TestClient, action: str) -> None:
    club_id = _make_club()
    job_id = _job_ready_for(action, club_id=club_id, creator=_importer(club_id))
    _authenticate_as(_make_user())

    _assert_hidden(client, job_id, action)


@pytest.mark.parametrize("action", ["approve", "apply"])
def test_creator_who_lost_permission_gets_404(client: TestClient, action: str) -> None:
    club_id = _make_club()
    creator = _make_user()
    job_id = _job_ready_for(action, club_id=club_id, creator=creator)
    _authenticate_as(creator)

    _assert_hidden(client, job_id, action)


@pytest.mark.parametrize("action", ["approve", "apply"])
@pytest.mark.parametrize("scope_type", ["own_groups", "self"])
def test_creator_with_non_all_scope_gets_404(
    client: TestClient, action: str, scope_type: str
) -> None:
    club_id = _make_club()
    creator = _make_user()
    _grant(creator, club_id=club_id, scope_type=scope_type)
    job_id = _job_ready_for(action, club_id=club_id, creator=creator)
    _authenticate_as(creator)

    _assert_hidden(client, job_id, action)


@pytest.mark.parametrize("action", ["approve", "apply"])
def test_administrator_of_another_club_gets_404(client: TestClient, action: str) -> None:
    club_a = _make_club()
    club_b = _make_club()
    job_id = _job_ready_for(action, club_id=club_b, creator=_importer(club_b))
    _authenticate_as(_admin(club_a))

    _assert_hidden(client, job_id, action)


@pytest.mark.parametrize("action", ["approve", "apply"])
def test_creator_scoped_to_another_club_gets_404(client: TestClient, action: str) -> None:
    club_a = _make_club()
    club_b = _make_club()
    creator = _importer(club_a)
    job_id = _job_ready_for(action, club_id=club_b, creator=creator)
    _authenticate_as(creator)

    _assert_hidden(client, job_id, action)


@pytest.mark.parametrize("club_scoped", [True, False])
def test_administrator_can_approve_and_apply_another_users_job(
    client: TestClient, club_scoped: bool
) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))
    import_id = _upload(client, _csv(_HEADER, "Anna,Ivanova,,,,,"))
    _preview(client, import_id)
    admin_id = _admin(club_id if club_scoped else None)
    _authenticate_as(admin_id)

    assert _post(client, import_id, "approve").status_code == 200
    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    [applied] = _audit("membership.import.applied")
    assert applied.actor_user_id == admin_id


def test_creator_can_approve_and_apply_own_job(client: TestClient) -> None:
    _setup(client)
    import_id = _approved_job(client, _csv(_HEADER, "Anna,Ivanova,,,,,"))

    assert _post(client, import_id, "apply").status_code == 200


@pytest.mark.parametrize("action", ["approve", "apply"])
def test_nonexistent_and_malformed_import_id(client: TestClient, action: str) -> None:
    _setup(client)

    assert _post(client, uuid.uuid4(), action).status_code == 404
    assert _post(client, "not-a-uuid", action).status_code == 422


# --- concurrency -------------------------------------------------------------------


def _insert_job(
    *, club_id: uuid.UUID, created_by_user_id: uuid.UUID, status: str = "uploaded"
) -> uuid.UUID:
    """A job created directly through the ORM, in any status (mirrors
    test_membership_imports_api.py's identical helper)."""
    with session_scope() as session:
        file_row = File(
            storage_key=f"imports/test/{uuid.uuid4()}",
            original_name="members.csv",
            mime_type="text/csv",
            size_bytes=0,
            checksum="0" * 64,
            storage_backend="local",
            created_by=created_by_user_id,
        )
        session.add(file_row)
        session.flush()
        job = ImportJob(
            club_id=club_id,
            created_by_user_id=created_by_user_id,
            source_file_id=file_row.id,
            source_format="csv",
            status=status,
        )
        session.add(job)
        session.commit()
        return job.id


def _run_concurrently(target, count: int = 2) -> list[object]:
    barrier = threading.Barrier(count)
    results: list[object] = [None] * count

    def worker(index: int) -> None:
        barrier.wait()
        try:
            results[index] = target()
        except Exception as exc:  # collected and asserted by the caller
            results[index] = exc

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


def test_concurrent_approves_approve_once(client: TestClient) -> None:
    _, user_id = _setup(client)
    import_id = _upload(client, _csv(_HEADER, "Anna,Ivanova,,,,,"))
    _preview(client, import_id)

    def approve() -> str:
        with session_scope() as session:
            job = session.get_one(ImportJob, uuid.UUID(import_id))
            return approve_import_job(session, job=job).status

    results = _run_concurrently(approve)

    assert sorted(type(result).__name__ for result in results) == [
        "ImportJobStatusConflictError",
        "str",
    ]
    assert "approved" in results
    assert _status(import_id) == "approved"


def test_concurrent_applies_apply_once(client: TestClient, storage_root: Path) -> None:
    _, user_id = _setup(client)
    import_id = _approved_job(
        client, _csv(_HEADER, "Anna,Ivanova,,,,anna@example.com,", "Boris,Petrov,,,,,")
    )
    before = _domain_counts()
    storage = LocalFileStorage(root=storage_root)

    def apply() -> str:
        with session_scope() as session:
            job = session.get_one(ImportJob, uuid.UUID(import_id))
            return run_import_apply(session, storage, job=job, actor_user_id=user_id).status

    results = _run_concurrently(apply)

    conflicts = [r for r in results if isinstance(r, ImportJobStatusConflictError)]
    assert len(conflicts) == 1, results
    assert conflicts[0].status in {"applying", "completed"}
    assert "completed" in results
    after = _domain_counts()
    assert after["Person"] == before["Person"] + 2
    assert after["User"] == before["User"] + 2
    assert after["ClubMembership"] == before["ClubMembership"] + 2
    assert len(_audit("membership.import.applied")) == 1
    assert _status(import_id) == "completed"


# --- D3: in-transaction duplicate re-check, login race, import concurrency --------


_DIMENSION_ROWS = {
    # dimension -> (conflicting Person values, CSV row of the imported person)
    "email": ({"email": "race@example.com"}, "Boris,Petrov,,,,race@example.com,"),
    "phone": ({"phone": "+7 900 321"}, "Boris,Petrov,,,+7 900 321,,"),
    "name_birth_date": (
        {"first_name": "Boris", "last_name": "Petrov", "birth_date": datetime.date(2011, 1, 2)},
        "Boris,Petrov,,2011-01-02,,,",
    ),
}
_DIMENSION_FIELD = {"email": "email", "phone": "phone", "name_birth_date": None}


def _codes(client: TestClient, import_id: str) -> list[str]:
    return [e["code"] for e in _errors(client, import_id)]


@pytest.mark.parametrize("dimension", sorted(_DIMENSION_ROWS))
def test_duplicate_committed_between_rows_is_caught_by_the_in_transaction_recheck(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, dimension: str
) -> None:
    """The batch evaluation at the start of apply sees no duplicate; while
    row 2 is being created, another workflow commits a Person that matches
    row 3. Only the per-row re-check can catch it."""
    _setup(client)
    conflicting, row = _DIMENSION_ROWS[dimension]
    import_id = _approved_job(client, _csv(_HEADER, "Anna,Ivanova,,,,,", row))
    conflict_ids: list[uuid.UUID] = []
    original = people_service_module.create_person_with_membership

    def create_person_with_membership(session, **kwargs):
        if kwargs["first_name"] == "Anna":
            conflict_ids.append(_make_person(**conflicting))
        return original(session, **kwargs)

    monkeypatch.setattr(
        people_service_module, "create_person_with_membership", create_person_with_membership
    )
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["statistics"]["created_records"] == 1
    assert body["statistics"]["skipped_records"] == 1
    assert body["statistics"]["updated_records"] == 0
    # Anna + the conflicting Person; Boris was not created.
    assert _count(Person) == before["Person"] + 2
    assert _count(User) == before["User"] + 1
    assert _count(ClubMembership) == before["ClubMembership"] + 1
    [conflict_id] = conflict_ids
    assert _user_of(conflict_id) is None
    assert _memberships_of(conflict_id) == []
    [warning] = [e for e in _errors(client, import_id) if e["code"] == "duplicate_exact"]
    assert warning["row_number"] == 3
    assert warning["severity"] == "warning"
    assert warning["field"] == _DIMENSION_FIELD[dimension]
    assert warning["matched_person_id"] == str(conflict_id)
    assert "import_apply_failed" not in _codes(client, import_id)


def test_recheck_and_creation_run_in_one_transaction_under_the_import_lock(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(client)
    import_id = _approved_job(client, _csv(_HEADER, "Anna,Ivanova,,,,,", "Boris,Petrov,,,,,"))
    events: list[tuple[str, int, int]] = []

    def probe(session) -> tuple[int, int]:
        txid = session.execute(select(func.txid_current())).scalar_one()
        held = session.execute(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND pid = pg_backend_pid() AND granted"
            )
        ).scalar_one()
        return txid, held

    original_recheck = apply_module.find_existing_person_matches
    original_create = people_service_module.create_person_with_membership

    def recheck(session, rows):
        events.append(("recheck", *probe(session)))
        return original_recheck(session, rows)

    def create(session, **kwargs):
        events.append(("create", *probe(session)))
        return original_create(session, **kwargs)

    monkeypatch.setattr(apply_module, "find_existing_person_matches", recheck)
    monkeypatch.setattr(people_service_module, "create_person_with_membership", create)

    assert _post(client, import_id, "apply").json()["status"] == "completed"

    assert [kind for kind, _, _ in events] == ["recheck", "create", "recheck", "create"]
    for (_, recheck_tx, recheck_locks), (_, create_tx, create_locks) in (
        (events[0], events[1]),
        (events[2], events[3]),
    ):
        assert recheck_tx == create_tx  # same row transaction
        assert recheck_locks == create_locks == 1  # the import lock is held
    assert events[0][1] != events[2][1]  # one transaction per row


def test_concurrent_login_uniqueness_violation_is_duplicate_exact_not_a_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another workflow commits a User with the row's login after the
    re-check and before the row's User insert: the database uniqueness
    violation is the email duplicate case."""
    _setup(client)
    import_id = _approved_job(
        client, _csv(_HEADER, "Anna,Ivanova,,,,,", "Boris,Petrov,,,,taken-late@example.com,")
    )
    racing_user_ids: list[uuid.UUID] = []
    original = account_provisioning.create_user_for_person

    def create_user_for_person(session, *, person_id, **kwargs):
        person = session.get(Person, person_id)
        if person is not None and person.email == "taken-late@example.com":
            racing_user_ids.append(_make_user(login="taken-late@example.com"))
        return original(session, person_id=person_id, **kwargs)

    monkeypatch.setattr(account_provisioning, "create_user_for_person", create_user_for_person)
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["statistics"]["created_records"] == 1
    assert body["statistics"]["skipped_records"] == 1
    assert _person_by_first_name("Boris") is None  # the row was rolled back
    # Anna + the racing User's own Person.
    assert _count(Person) == before["Person"] + 2
    [racing_user_id] = racing_user_ids
    with session_scope() as session:
        racing_person_id = session.get(User, racing_user_id).person_id
    [warning] = [e for e in _errors(client, import_id) if e["code"] == "duplicate_exact"]
    assert (warning["row_number"], warning["field"]) == (3, "email")
    assert warning["matched_person_id"] == str(racing_person_id)
    assert "import_apply_failed" not in _codes(client, import_id)
    [applied] = _audit("membership.import.applied")
    assert applied.details["failed_records"] == 0


@pytest.mark.parametrize("dimension", sorted(_DIMENSION_ROWS))
def test_concurrent_import_jobs_never_both_create_the_same_participant(
    client: TestClient, storage_root: Path, monkeypatch: pytest.MonkeyPatch, dimension: str
) -> None:
    """Job A is inside its row transaction (lock held, Person not yet
    committed) when job B starts: B must wait for the import lock, then its
    re-check sees A's Person — duplicate_exact, skipped, never a failure."""
    _, user_id = _setup(client)
    conflicting, row = _DIMENSION_ROWS[dimension]
    job_a = _approved_job(client, _csv(_HEADER, row))
    job_b = _approved_job(client, _csv(_HEADER, row))
    storage = LocalFileStorage(root=storage_root)
    a_inside = threading.Event()
    original = people_service_module.create_person_with_membership

    def create_person_with_membership(session, **kwargs):
        if threading.current_thread().name == "job-a":
            a_inside.set()
            time.sleep(0.5)
        return original(session, **kwargs)

    monkeypatch.setattr(
        people_service_module, "create_person_with_membership", create_person_with_membership
    )
    before = _domain_counts()
    results: dict[str, object] = {}

    def apply(import_id: str, name: str) -> None:
        if name == "job-b":
            assert a_inside.wait(timeout=30)
        try:
            with session_scope() as session:
                job = session.get_one(ImportJob, uuid.UUID(import_id))
                results[name] = run_import_apply(
                    session, storage, job=job, actor_user_id=user_id
                ).status
        except Exception as exc:  # asserted below
            results[name] = exc

    threads = [
        threading.Thread(target=apply, args=(job_a, "job-a"), name="job-a"),
        threading.Thread(target=apply, args=(job_b, "job-b"), name="job-b"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert results == {"job-a": "completed", "job-b": "completed"}
    assert _count(Person) == before["Person"] + 1
    assert _count(User) == before["User"] + 1
    assert _count(ClubMembership) == before["ClubMembership"] + 1
    report_a = client.get(f"{_URL}/{job_a}").json()["statistics"]
    report_b = client.get(f"{_URL}/{job_b}").json()["statistics"]
    assert (report_a["created_records"], report_a["skipped_records"]) == (1, 0)
    assert (report_b["created_records"], report_b["skipped_records"]) == (0, 1)
    assert _codes(client, job_b) == ["duplicate_exact"]
    assert _codes(client, job_a) == []
    assert len(_audit("membership.import.applied")) == 2


def test_external_id_duplicates_are_intra_file_only(client: TestClient) -> None:
    _setup(client)
    first = _approved_job(
        client,
        _csv(_HEADER, "Anna,Ivanova,,,,,X-1", "Anya,Orlova,,,,,X-1", "Boris,Petrov,,,,,X-2"),
    )

    body = _post(client, first, "apply").json()

    assert body["statistics"]["created_records"] == 1
    assert body["statistics"]["skipped_records"] == 2
    assert _person_by_first_name("Anna") is None and _person_by_first_name("Anya") is None
    # external_id is never compared with persisted data (no persisted
    # external identifier exists), so another job's X-2 is not a duplicate.
    second = _approved_job(client, _csv(_HEADER, "Vera,Sokolova,,,,,X-2"))
    body = _post(client, second, "apply").json()
    assert body["statistics"]["created_records"] == 1
    assert body["statistics"]["skipped_records"] == 0


def test_unexpected_batch_level_failure_is_import_apply_failed_without_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(client)
    import_id = _approved_job(client, _csv(_HEADER, "Anna,Ivanova,,,,,"))

    def broken(*args, **kwargs):
        raise RuntimeError("simulated batch failure")

    monkeypatch.setattr(apply_module, "evaluate_source", broken)
    before = _domain_counts()

    response = _post(client, import_id, "apply")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert _domain_counts() == before
    [failure] = [e for e in _errors(client, import_id) if e["code"] == "import_apply_failed"]
    assert failure["row_number"] is None
    assert failure["severity"] == "error"
    assert "simulated" not in failure["message"]
    [applied] = _audit("membership.import.applied")
    assert applied.outcome == "failure"
