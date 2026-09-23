"""Integration tests for the participant import preview (TH-0118.2;
docs/05-api/people-api.md §22):

    POST /api/v1/memberships/imports/{import_id}/preview
    GET  /api/v1/memberships/imports/{import_id}/errors?severity=...

Against the REAL shipped app (app.main.app) and a real PostgreSQL
database, mirroring tests/integration/test_membership_imports_api.py's
fixture conventions (local, duplicated helpers; `get_file_storage`
overridden to a `LocalFileStorage` rooted at `tmp_path`). Jobs are created
through the real upload endpoint, so the preview reads the source file
through the real FileStorage path.
"""

import datetime
import io
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select

import app.imports.preview as preview_module
from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.attendance import Attendance
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.documents import File
from app.db.events import EventParticipation
from app.db.groups import GroupInstructorAssignment, GroupMembership
from app.db.identity import Club, ClubMembership, GuardianRelationship, Person, User
from app.db.imports import ImportJob, ImportJobError
from app.db.session import session_scope
from app.main import app
from app.storage.local import LocalFileStorage, get_file_storage

from .conftest import requires_postgres

pytestmark = requires_postgres

_URL = "/api/v1/memberships/imports"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def client(storage_root: Path) -> TestClient:
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(root=storage_root)
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def transitions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records every status the preview moves a job to, in order."""
    recorded: list[str] = []
    original = preview_module.transition_import_job_status

    def recording(session, *, job, new_status):
        result = original(session, job=job, new_status=new_status)
        recorded.append(new_status)
        return result

    monkeypatch.setattr(preview_module, "transition_import_job_status", recording)
    return recorded


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


def _importer(club_id: uuid.UUID) -> uuid.UUID:
    user_id = _make_user()
    with session_scope() as session:
        permission = session.execute(
            select(Permission).where(Permission.code == "membership.import")
        ).scalar_one()
        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
        session.add(role)
        session.flush()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(
            UserRoleAssignment(user_id=user_id, role_id=role.id, scope_type="all", club_id=club_id)
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


def _xlsx(*sheets: list[list[object]]) -> bytes:
    workbook = Workbook()
    first = workbook.active
    for index, rows in enumerate(sheets):
        sheet = first if index == 0 else workbook.create_sheet(f"Sheet{index + 1}")
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


def _preview(client: TestClient, import_id: str):
    return client.post(f"{_URL}/{import_id}/preview", headers=_csrf_headers(client))


def _errors(client: TestClient, import_id: str, **params) -> list[dict]:
    response = client.get(f"{_URL}/{import_id}/errors", params={"page_size": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _setup(client: TestClient) -> uuid.UUID:
    club_id = _make_club()
    user_id = _importer(club_id)
    _authenticate_as(user_id)
    return user_id


def _job(import_id: str) -> ImportJob:
    with session_scope() as session:
        job = session.get(ImportJob, uuid.UUID(import_id))
        assert job is not None
        session.expunge(job)
        return job


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


# --- happy path + lifecycle -------------------------------------------------


def test_valid_csv_preview_reaches_preview_ready(
    client: TestClient, transitions: list[str]
) -> None:
    _setup(client)
    import_id = _upload(
        client,
        _csv(
            "first_name,last_name,middle_name,birth_date,phone,email,external_id",
            "Anna,Ivanova,Petrovna,2010-05-01,+7 900 111,anna@example.com,E1",
            "Boris,Petrov,,,,,",
        ),
    )

    response = _preview(client, import_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["import_id"] == import_id
    assert body["status"] == "preview_ready"
    assert body["statistics"] == {
        "total_records": 2,
        "valid_records": 2,
        "invalid_records": 0,
        "created_records": None,
        "updated_records": None,
        "skipped_records": None,
        "error_count": 0,
    }
    assert transitions == ["parsing", "validating", "preview_ready"]
    assert _errors(client, import_id) == []
    assert client.get(f"{_URL}/{import_id}").json()["status"] == "preview_ready"


def test_valid_xlsx_preview_uses_only_the_first_worksheet(client: TestClient) -> None:
    _setup(client)
    content = _xlsx(
        [
            ["first_name", "last_name", "birth_date", "phone"],
            ["Anna", "Ivanova", datetime.date(2010, 5, 1), 79001112233],
            ["Boris", "Petrov", None, None],
        ],
        [["first_name", "last_name"], ["Ignored", "Row"], ["", ""]],
        [["garbage header"]],
    )
    import_id = _upload(client, content, filename="members.xlsx")

    response = _preview(client, import_id)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "preview_ready"
    assert response.json()["statistics"]["total_records"] == 2
    assert response.json()["statistics"]["valid_records"] == 2


def test_header_only_file_is_preview_ready_with_zero_records(client: TestClient) -> None:
    _setup(client)
    import_id = _upload(client, _csv("first_name,last_name"))

    body = _preview(client, import_id).json()

    assert body["status"] == "preview_ready"
    assert body["statistics"]["total_records"] == 0


def test_empty_rows_are_not_records(client: TestClient) -> None:
    _setup(client)
    import_id = _upload(client, _csv("first_name,last_name", "", "Anna,Ivanova", ",", " , "))

    body = _preview(client, import_id).json()

    assert body["statistics"]["total_records"] == 1


# --- row-level errors -------------------------------------------------------


def test_row_errors_make_rows_invalid_but_the_job_still_reaches_preview_ready(
    client: TestClient,
) -> None:
    _setup(client)
    import_id = _upload(
        client,
        _csv(
            "first_name,last_name,birth_date,phone,email",
            ",Ivanova,,,",  # row 2: first_name missing
            "Boris,,,,",  # row 3: last_name missing
            "Clara,Sidorova,01.05.2010,,not-an-email",  # row 4: two errors
            f"Dmitry,Orlov,,{'1' * 33},",  # row 5: phone too long
            "Elena,Kuznetsova,2011-02-03,+7 900,elena@example.com",  # row 6: valid
        ),
    )

    body = _preview(client, import_id).json()

    assert body["status"] == "preview_ready"
    assert body["statistics"]["total_records"] == 5
    assert body["statistics"]["invalid_records"] == 4
    assert body["statistics"]["valid_records"] == 1
    assert body["statistics"]["error_count"] == 5
    errors = _errors(client, import_id)
    # Entries are ordered by row; two entries of the same row have no
    # defined order between them, so compare as a sorted list.
    assert sorted((e["row_number"], e["field"], e["code"], e["severity"]) for e in errors) == [
        (2, "first_name", "required_field_missing", "error"),
        (3, "last_name", "required_field_missing", "error"),
        (4, "birth_date", "invalid_birth_date", "error"),
        (4, "email", "invalid_email", "error"),
        (5, "phone", "value_too_long", "error"),
    ]
    assert [e["row_number"] for e in errors] == [2, 3, 4, 4, 5]
    assert all(e["matched_person_id"] is None for e in errors)
    assert "not-an-email" not in str(errors)


# --- parsing / validation failures ------------------------------------------


@pytest.mark.parametrize(
    "content,expected_code,expected_field",
    [
        (b"", "import_header_missing", None),
        (b"first_name,surname\nAnna,Ivanova\n", "import_header_unknown_column", "surname"),
        (b"first_name,last_name,last_name\n", "import_header_duplicate_column", "last_name"),
        (b"first_name,email\nAnna,a@b.c\n", "import_header_missing_required_column", "last_name"),
        (b'first_name,last_name\n"Anna,Ivanova\n', "import_file_malformed", None),
        ("first_name,last_name\nАнна,Иванова\n".encode("cp1251"), "import_file_unreadable", None),
    ],
)
def test_csv_parsing_failure_fails_the_job(
    client: TestClient,
    transitions: list[str],
    content: bytes,
    expected_code: str,
    expected_field: str | None,
) -> None:
    _setup(client)
    import_id = _upload(client, content)

    response = _preview(client, import_id)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "import_validation_failed"
    assert error["details"] == {"import_id": import_id, "status": "failed"}
    assert transitions == ["parsing", "failed"]
    assert _job(import_id).status == "failed"
    assert _job(import_id).total_records is None
    errors = _errors(client, import_id)
    assert [(e["code"], e["field"], e["severity"]) for e in errors] == [
        (expected_code, expected_field, "error")
    ]
    assert client.get(f"{_URL}/{import_id}").json()["statistics"]["error_count"] == 1


@pytest.mark.parametrize(
    "content,expected_code",
    [
        (b"PK\x03\x04 not really a workbook", "import_file_unreadable"),
        (b"first_name,last_name\nAnna,Ivanova\n", "import_file_unreadable"),
        (_xlsx([], [["first_name", "last_name"], ["Anna", "Ivanova"]]), "import_header_missing"),
        (_xlsx([["first_name", "nickname"], ["Anna", "Ann"]]), "import_header_unknown_column"),
    ],
)
def test_xlsx_parsing_failure_fails_the_job(
    client: TestClient, transitions: list[str], content: bytes, expected_code: str
) -> None:
    _setup(client)
    import_id = _upload(client, content, filename="members.xlsx")

    response = _preview(client, import_id)

    assert response.status_code == 422
    assert transitions == ["parsing", "failed"]
    assert [e["code"] for e in _errors(client, import_id)] == [expected_code]


def test_missing_source_object_fails_the_job(client: TestClient, storage_root: Path) -> None:
    _setup(client)
    import_id = _upload(client, _csv("first_name,last_name", "Anna,Ivanova"))
    with session_scope() as session:
        storage_key = session.get(File, _job(import_id).source_file_id).storage_key
    LocalFileStorage(root=storage_root).delete(storage_key)

    response = _preview(client, import_id)

    assert response.status_code == 422
    assert _job(import_id).status == "failed"
    assert [e["code"] for e in _errors(client, import_id)] == ["import_file_unreadable"]


def test_validation_failure_fails_the_job_and_keeps_no_partial_rows(
    client: TestClient, transitions: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(client)
    import_id = _upload(client, _csv("first_name,last_name,email", ",Ivanova,bad"))

    def broken_lookup(session, rows):
        raise RuntimeError("lookup unavailable")

    monkeypatch.setattr(preview_module, "find_existing_person_matches", broken_lookup)

    response = _preview(client, import_id)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "import_validation_failed"
    assert transitions == ["parsing", "validating", "failed"]
    job = _job(import_id)
    assert job.status == "failed"
    assert (job.total_records, job.valid_records, job.invalid_records) == (None, None, None)
    # Only the job-level failure — no half-written row errors.
    assert [(e["row_number"], e["code"]) for e in _errors(client, import_id)] == [
        (None, "import_validation_failed")
    ]
    assert "lookup unavailable" not in str(_errors(client, import_id))


# --- 409 / 404 / auth -------------------------------------------------------


@pytest.mark.parametrize("status", ["parsing", "validating", "preview_ready", "failed"])
def test_preview_from_non_uploaded_job_is_409_and_changes_nothing(
    client: TestClient, status: str
) -> None:
    _setup(client)
    import_id = _upload(client, _csv("first_name,last_name", "Anna,Ivanova"))
    with session_scope() as session:
        session.get(ImportJob, uuid.UUID(import_id)).status = status
        session.commit()

    response = _preview(client, import_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_import_job_status_transition"
    assert _job(import_id).status == status
    assert _errors(client, import_id) == []


def test_second_preview_is_409(client: TestClient) -> None:
    _setup(client)
    import_id = _upload(client, _csv("first_name,last_name", "Anna,Ivanova"))
    assert _preview(client, import_id).status_code == 200

    assert _preview(client, import_id).status_code == 409
    assert _job(import_id).status == "preview_ready"


def test_preview_of_nonexistent_job_is_404(client: TestClient) -> None:
    _setup(client)
    response = _preview(client, str(uuid.uuid4()))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_preview_of_someone_elses_job_is_404_and_does_not_start_it(client: TestClient) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))
    import_id = _upload(client, _csv("first_name,last_name", "Anna,Ivanova"))

    _authenticate_as(_importer(club_id))
    response = _preview(client, import_id)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert _job(import_id).status == "uploaded"


def test_preview_without_permission_is_404(client: TestClient) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))
    import_id = _upload(client, _csv("first_name,last_name", "Anna,Ivanova"))

    _authenticate_as(_make_user())

    assert _preview(client, import_id).status_code == 404
    assert _job(import_id).status == "uploaded"


def test_preview_requires_authentication_and_csrf(client: TestClient) -> None:
    _setup(client)
    import_id = _upload(client, _csv("first_name,last_name", "Anna,Ivanova"))

    assert client.post(f"{_URL}/{import_id}/preview").status_code == 403  # no CSRF token
    app.dependency_overrides.pop(get_current_principal)
    assert _preview(client, import_id).status_code == 401
    assert _job(import_id).status == "uploaded"


# --- duplicates -------------------------------------------------------------


def _duplicates(client: TestClient, import_id: str) -> list[tuple]:
    return [
        (e["row_number"], e["field"], e["matched_person_id"])
        for e in _errors(client, import_id, severity="warning")
        if e["code"] == "duplicate_exact"
    ]


def test_duplicates_against_existing_persons_are_warnings_with_matched_person_id(
    client: TestClient,
) -> None:
    _setup(client)
    by_email = _make_person(email=" Anna@Example.com ")
    by_phone = _make_person(phone=" +7 900 222 ")
    by_name = _make_person(
        first_name="Clara", last_name="Sidorova", birth_date=datetime.date(2009, 1, 2)
    )
    with session_scope() as session:
        login_owner = session.get(User, _make_user(login="Login.Owner@Example.com")).person_id
    import_id = _upload(
        client,
        _csv(
            "first_name,last_name,birth_date,phone,email",
            "Anna,Newname,,,anna@example.com",  # row 2 -> by_email (Person.email)
            "Boris,Petrov,,+7 900 222,",  # row 3 -> by_phone
            " Clara , Sidorova ,2009-01-02,,",  # row 4 -> by_name
            "Dmitry,Orlov,,,login.owner@example.com",  # row 5 -> User login identifier
            "Elena,Unique,,,elena@example.com",  # row 6 -> no match
        ),
    )

    body = _preview(client, import_id).json()

    assert body["status"] == "preview_ready"
    # duplicate_exact is a warning: every row stays valid.
    assert body["statistics"]["valid_records"] == 5
    assert body["statistics"]["invalid_records"] == 0
    assert body["statistics"]["error_count"] == 0
    assert _duplicates(client, import_id) == [
        (2, "email", str(by_email)),
        (3, "phone", str(by_phone)),
        (4, None, str(by_name)),
        (5, "email", str(login_owner)),
    ]
    warnings = _errors(client, import_id, severity="warning")
    assert {w["severity"] for w in warnings} == {"warning"}
    # Only the id of the matched Person — none of their data.
    text = str(warnings)
    for leaked in ("Anna@Example.com", "Existing", "Sidorova", "Login.Owner", "+7 900 222"):
        assert leaked not in text


def test_in_file_duplicates_mark_every_conflicting_row_without_person_id(
    client: TestClient,
) -> None:
    _setup(client)
    import_id = _upload(
        client,
        _csv(
            "first_name,last_name,birth_date,phone,email,external_id",
            "Anna,Ivanova,,,,EXT-1",  # 2
            "Boris,Petrov,,,b@example.com,",  # 3
            "Clara,Sidorova,,+7 1,,",  # 4
            "Dmitry,Orlov,2000-01-01,,,",  # 5
            "Anna2,Ivanova2,,,,EXT-1",  # 6  external_id with 2
            "Boris2,Petrov2,,,B@Example.com,",  # 7  email with 3
            "Clara2,Sidorova2,,+7 1,,",  # 8  phone with 4
            "Dmitry,Orlov,2000-01-01,,,",  # 9  name+birth_date with 5
        ),
    )

    body = _preview(client, import_id).json()

    assert body["statistics"]["valid_records"] == 8
    assert _duplicates(client, import_id) == [
        (2, "external_id", None),
        (3, "email", None),
        (4, "phone", None),
        (5, None, None),
        (6, "external_id", None),
        (7, "email", None),
        (8, "phone", None),
        (9, None, None),
    ]


def test_external_id_is_never_matched_against_existing_data(client: TestClient) -> None:
    _setup(client)
    _make_person(first_name="Anna", last_name="Ivanova")
    import_id = _upload(client, _csv("first_name,last_name,external_id", "Zed,Zulu,EXT-1"))

    _preview(client, import_id)

    assert _duplicates(client, import_id) == []


def test_fuzzy_matches_are_not_reported(client: TestClient) -> None:
    _setup(client)
    _make_person(first_name="Anna", last_name="Ivanova", birth_date=datetime.date(2010, 5, 1))
    import_id = _upload(
        client,
        _csv(
            "first_name,last_name,birth_date",
            "Ana,Ivanova,2010-05-01",
            "anna,ivanova,2010-05-01",
            "Anna,Ivanova,",
            "Anna,Ivanova,2010-05-02",
        ),
    )

    _preview(client, import_id)

    assert _errors(client, import_id) == []


def test_severity_filter(client: TestClient) -> None:
    _setup(client)
    import_id = _upload(
        client,
        _csv("first_name,last_name,email", ",Ivanova,", "Anna,Ivanova,a@x.io", "Ann,Iv,a@x.io"),
    )
    _preview(client, import_id)

    everything = _errors(client, import_id)
    errors = _errors(client, import_id, severity="error")
    warnings = _errors(client, import_id, severity="warning")

    assert [e["code"] for e in errors] == ["required_field_missing"]
    assert [w["code"] for w in warnings] == ["duplicate_exact", "duplicate_exact"]
    assert len(everything) == 3
    assert {e["severity"] for e in everything} == {"error", "warning"}
    response = client.get(f"{_URL}/{import_id}/errors", params={"severity": "info"})
    assert response.status_code == 422
    paged = client.get(
        f"{_URL}/{import_id}/errors", params={"severity": "warning", "page_size": 1}
    ).json()
    assert paged["pagination"] == {"page": 1, "page_size": 1, "total": 2, "pages": 2}


def test_row_with_error_and_warning_is_invalid(client: TestClient) -> None:
    _setup(client)
    import_id = _upload(
        client, _csv("first_name,last_name,email", ",Ivanova,a@x.io", "Anna,Ivanova,a@x.io")
    )

    body = _preview(client, import_id).json()

    assert body["statistics"]["invalid_records"] == 1
    assert body["statistics"]["valid_records"] == 1
    assert body["statistics"]["error_count"] == 1


# --- dry-run / domain integrity ---------------------------------------------


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
    AuditLog,
)


def _counts() -> dict[str, int]:
    with session_scope() as session:
        return {
            model.__name__: session.execute(select(func.count()).select_from(model)).scalar_one()
            for model in _DOMAIN_MODELS
        }


def _person_snapshot(person_id: uuid.UUID) -> tuple:
    with session_scope() as session:
        person = session.get(Person, person_id)
        return (
            person.first_name,
            person.last_name,
            person.middle_name,
            person.birth_date,
            person.phone,
            person.email,
            person.updated_at,
        )


def test_preview_creates_and_changes_no_domain_entity(client: TestClient) -> None:
    importer = _setup(client)
    existing = _make_person(
        first_name="Anna",
        last_name="Ivanova",
        birth_date=datetime.date(2010, 5, 1),
        phone="+7 900",
        email="anna@example.com",
    )
    before_person = _person_snapshot(existing)
    with session_scope() as session:
        before_importer_person = _person_snapshot(session.get(User, importer).person_id)
    before = _counts()
    import_id = _upload(
        client,
        _csv(
            "first_name,last_name,middle_name,birth_date,phone,email,external_id",
            # Exact duplicate of `existing` by every rule, with different
            # other data: must NOT update/merge/reuse it.
            "Anna,Ivanova,Changed,2010-05-01,+7 900,anna@example.com,E1",
            "New,Person,,2012-01-01,,new@example.com,E2",
            "NoEmail,Person,,,,,",
        ),
    )

    response = _preview(client, import_id)

    assert response.status_code == 200, response.text
    assert _counts() == before
    assert _person_snapshot(existing) == before_person
    with session_scope() as session:
        assert _person_snapshot(session.get(User, importer).person_id) == before_importer_person
    assert any(w["matched_person_id"] == str(existing) for w in _errors(client, import_id))


def test_failed_preview_creates_no_domain_entity(client: TestClient) -> None:
    _setup(client)
    before = _counts()
    import_id = _upload(client, b"first_name,surname\nAnna,Ivanova\n")

    assert _preview(client, import_id).status_code == 422
    assert _counts() == before


def test_import_job_error_rows_belong_only_to_their_job(client: TestClient) -> None:
    _setup(client)
    first = _upload(client, _csv("first_name,last_name", ",A"))
    second = _upload(client, _csv("first_name,last_name", "B,"))
    _preview(client, first)
    _preview(client, second)

    with session_scope() as session:
        rows = session.execute(select(ImportJobError.import_job_id, ImportJobError.field)).all()
    assert sorted((str(job_id), field) for job_id, field in rows) == sorted(
        [(first, "first_name"), (second, "last_name")]
    )
