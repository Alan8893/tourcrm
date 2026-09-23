"""Integration tests for the participant import job foundation
(TH-0118.1 / Issue #185; docs/05-api/people-api.md §22):

    POST /api/v1/memberships/imports
    GET  /api/v1/memberships/imports/{import_id}
    GET  /api/v1/memberships/imports/{import_id}/errors

plus the service-level lifecycle transition, the `import_jobs`/
`import_job_errors` CHECK constraints, and the migration's upgrade/
downgrade round-trip. Against the REAL shipped app (app.main.app) and a
real PostgreSQL database, mirroring tests/integration/
test_person_documents_api.py's fixture conventions (local, duplicated
helpers; `get_file_storage` overridden to a `LocalFileStorage` rooted at
`tmp_path`).

Run with a reachable PostgreSQL instance:

    export TEST_DATABASE_URL=postgresql+psycopg://tourcrm:***@localhost:5432/tourcrm_test
    pytest tests/integration -v
"""

import datetime
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, get_current_principal
from app.db.audit import AuditLog
from app.db.authorization import Permission, Role, RolePermission, UserRoleAssignment
from app.db.documents import File
from app.db.identity import Club, ClubMembership, Person, User
from app.db.imports import ImportJob, ImportJobError
from app.db.session import session_scope
from app.imports.lifecycle import (
    InvalidImportJobStatusError,
    InvalidImportJobStatusTransitionError,
)
from app.imports.service import transition_import_job_status
from app.main import app
from app.storage.local import LocalFileStorage, get_file_storage

from ._schema_reset import run_alembic
from .conftest import requires_postgres

pytestmark = requires_postgres

_URL = "/api/v1/memberships/imports"
_BEFORE_MIGRATION_REVISION = "c70bcac43032"
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


# --- fixtures / factories ---------------------------------------------------


def _make_club() -> uuid.UUID:
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.commit()
        return club.id


def _make_user() -> uuid.UUID:
    with session_scope() as session:
        person = Person(last_name="Ivanova", first_name=f"P-{uuid.uuid4().hex[:8]}")
        user = User(
            person=person,
            login_identifier=f"user-{uuid.uuid4().hex[:8]}@example.com",
            status="active",
        )
        session.add_all([person, user])
        session.commit()
        return user.id


def _grant_permission(
    user_id: uuid.UUID,
    permission_code: str,
    scope_type: str = "all",
    club_id: uuid.UUID | None = None,
) -> None:
    """Ad hoc grant via a throwaway, non-system role — isolated from the
    real admin RolePermission wiring (mirrors test_person_account_api.py's
    identical helper)."""
    with session_scope() as session:
        permission = session.execute(
            select(Permission).where(Permission.code == permission_code)
        ).scalar_one_or_none()
        if permission is None:
            permission = Permission(code=permission_code)
            session.add(permission)
            session.commit()
        role = Role(code=f"role-{uuid.uuid4().hex[:8]}", name="Test role")
        session.add(role)
        session.commit()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=role.id, scope_type=scope_type, club_id=club_id
            )
        )
        session.commit()


def _assign_real_admin(user_id: uuid.UUID, club_id: uuid.UUID | None) -> None:
    """Assign the seeded, canonical system `admin` role — whose
    `membership.import` grant comes from this slice's own migration, not
    from a test-local RolePermission."""
    with session_scope() as session:
        admin_role = session.execute(select(Role).where(Role.code == "admin")).scalar_one()
        session.add(
            UserRoleAssignment(
                user_id=user_id, role_id=admin_role.id, scope_type="all", club_id=club_id
            )
        )
        session.commit()


def _authenticate_as(user_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        user_id=user_id, session_id=uuid.uuid4()
    )


def _csrf_headers(client: TestClient) -> dict:
    client.cookies.set("csrf_token", "test-csrf-token")
    return {"X-CSRF-Token": "test-csrf-token"}


def _upload(
    client: TestClient,
    *,
    filename: str = "members.csv",
    content: bytes = b"last_name,first_name\nIvanova,Anna\n",
    mime_type: str = "text/csv",
):
    return client.post(
        _URL,
        files={"file": (filename, content, mime_type)},
        headers=_csrf_headers(client),
    )


def _importer(club_id: uuid.UUID) -> uuid.UUID:
    """A non-admin user holding `membership.import` + `all` for `club_id`
    through a throwaway role."""
    user_id = _make_user()
    _grant_permission(user_id, "membership.import", scope_type="all", club_id=club_id)
    return user_id


def _admin(club_id: uuid.UUID | None) -> uuid.UUID:
    user_id = _make_user()
    _assign_real_admin(user_id, club_id)
    return user_id


def _insert_job(
    *, club_id: uuid.UUID, created_by_user_id: uuid.UUID, status: str = "uploaded"
) -> uuid.UUID:
    """A job created directly through the ORM — used where POST cannot
    reach (e.g. a job in a second Club, which would violate the single-Club
    invariant POST relies on)."""
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


def _add_errors(job_id: uuid.UUID, rows: list[tuple[int | None, str | None, str]]) -> None:
    with session_scope() as session:
        for row_number, field, code in rows:
            session.add(
                ImportJobError(
                    import_job_id=job_id,
                    row_number=row_number,
                    field=field,
                    code=code,
                    message=f"{code} at row {row_number}",
                    severity="error",
                )
            )
        session.commit()


def _count(model) -> int:
    with session_scope() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


# --- POST: creation ---------------------------------------------------------


def test_create_csv_import_job_returns_201_with_uploaded_status(
    client: TestClient, storage_root: Path
) -> None:
    club_id = _make_club()
    user_id = _importer(club_id)
    _authenticate_as(user_id)

    content = b"last_name,first_name\nIvanova,Anna\n"
    response = _upload(client, filename="members.csv", content=content)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"import_id", "status", "format", "created_at"}
    assert body["status"] == "uploaded"
    assert body["format"] == "csv"
    datetime.datetime.fromisoformat(body["created_at"])

    with session_scope() as session:
        job = session.get(ImportJob, uuid.UUID(body["import_id"]))
        assert job is not None
        # Club-bound to the installation's one Club; records its creator.
        assert job.club_id == club_id
        assert job.created_by_user_id == user_id
        assert job.status == "uploaded"
        assert job.source_format == "csv"
        # Source file goes through the existing File/FileStorage subsystem
        # and the job holds only the reference.
        file_row = session.get(File, job.source_file_id)
        assert file_row is not None
        assert file_row.original_name == "members.csv"
        assert file_row.mime_type == "text/csv"
        assert file_row.size_bytes == len(content)
        assert file_row.storage_backend == "local"
        assert file_row.created_by == user_id
        storage_key = file_row.storage_key

    assert LocalFileStorage(root=storage_root).get(storage_key) == content
    assert "storage_key" not in response.text


def test_create_xlsx_import_job_is_accepted(client: TestClient) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))

    response = _upload(
        client, filename="Members.XLSX", content=b"PK\x03\x04not-parsed", mime_type=_XLSX_MIME
    )

    assert response.status_code == 201, response.text
    assert response.json()["format"] == "xlsx"
    assert response.json()["status"] == "uploaded"


@pytest.mark.parametrize(
    "filename,mime_type",
    [
        ("members.xls", "application/vnd.ms-excel"),
        ("members.txt", "text/plain"),
        ("members.pdf", "application/pdf"),
        ("members", "text/csv"),
    ],
)
def test_unsupported_format_is_rejected_with_422_and_nothing_is_stored(
    client: TestClient, storage_root: Path, filename: str, mime_type: str
) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))

    response = _upload(client, filename=filename, mime_type=mime_type)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_import_format"
    assert _count(ImportJob) == 0
    assert _count(File) == 0
    assert not any(path.is_file() for path in storage_root.rglob("*"))


def test_missing_file_is_a_validation_error(client: TestClient) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))

    response = client.post(_URL, headers=_csrf_headers(client))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_upload_does_not_parse_or_apply_and_creates_no_participant_records(
    client: TestClient,
) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))
    before = (_count(Person), _count(User), _count(ClubMembership), _count(UserRoleAssignment))

    # Content that no parser would accept: upload must still succeed,
    # because upload never reads or validates the content.
    response = _upload(client, filename="members.csv", content=b"\x00\xff not csv at all")

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "uploaded"
    after = (_count(Person), _count(User), _count(ClubMembership), _count(UserRoleAssignment))
    assert after == before
    assert _count(ImportJobError) == 0


def test_create_import_job_writes_no_audit_record(client: TestClient) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))
    audit_before = _count(AuditLog)

    response = _upload(client)

    assert response.status_code == 201, response.text
    assert _count(AuditLog) == audit_before


# --- POST: authentication / authorization -----------------------------------


def test_create_requires_authentication(client: TestClient) -> None:
    _make_club()
    response = _upload(client)
    assert response.status_code == 401


def test_create_requires_csrf_token(client: TestClient) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))

    response = client.post(_URL, files={"file": ("members.csv", b"a\n", "text/csv")})

    assert response.status_code == 403
    assert _count(ImportJob) == 0


def test_create_without_membership_import_is_forbidden(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _make_user()
    # Adjacent permissions explicitly do NOT grant import.
    for code in ("membership.manage", "person.create", "role.manage", "account.manage"):
        _grant_permission(user_id, code, scope_type="all", club_id=club_id)
    _authenticate_as(user_id)

    response = _upload(client)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert _count(ImportJob) == 0
    assert _count(File) == 0


@pytest.mark.parametrize("scope_type", ["own_groups", "self", "children", "own_events", "none"])
def test_create_with_non_all_scope_is_forbidden(client: TestClient, scope_type: str) -> None:
    club_id = _make_club()
    user_id = _make_user()
    _grant_permission(user_id, "membership.import", scope_type=scope_type, club_id=club_id)
    _authenticate_as(user_id)

    response = _upload(client)

    assert response.status_code == 403
    assert _count(ImportJob) == 0


def test_create_with_expired_assignment_is_forbidden(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _importer(club_id)
    with session_scope() as session:
        for assignment in session.execute(
            select(UserRoleAssignment).where(UserRoleAssignment.user_id == user_id)
        ).scalars():
            assignment.valid_from = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
            assignment.valid_to = datetime.datetime(2021, 1, 1, tzinfo=datetime.timezone.utc)
        session.commit()
    _authenticate_as(user_id)

    assert _upload(client).status_code == 403


@pytest.mark.parametrize("club_scoped", [True, False])
def test_real_admin_role_can_create_import_job(client: TestClient, club_scoped: bool) -> None:
    """MVP: `membership.import` is granted to the seeded `admin` role."""
    club_id = _make_club()
    admin_id = _admin(club_id if club_scoped else None)
    _authenticate_as(admin_id)

    response = _upload(client)

    assert response.status_code == 201, response.text


@pytest.mark.parametrize("role_code", ["instructor", "member", "guardian"])
def test_other_baseline_roles_cannot_create_import_job(client: TestClient, role_code: str) -> None:
    club_id = _make_club()
    user_id = _make_user()
    with session_scope() as session:
        role = session.execute(select(Role).where(Role.code == role_code)).scalar_one()
        session.add(
            UserRoleAssignment(user_id=user_id, role_id=role.id, scope_type="all", club_id=club_id)
        )
        session.commit()
    _authenticate_as(user_id)

    assert _upload(client).status_code == 403


# --- GET status -------------------------------------------------------------


def test_creator_can_get_own_import_job_with_initial_statistics(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _importer(club_id)
    _authenticate_as(user_id)
    created = _upload(client, filename="members.xlsx", mime_type=_XLSX_MIME).json()

    response = client.get(f"{_URL}/{created['import_id']}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["import_id"] == created["import_id"]
    assert body["club_id"] == str(club_id)
    assert body["created_by_user_id"] == str(user_id)
    assert body["status"] == "uploaded"
    assert body["format"] == "xlsx"
    assert body["created_at"] == created["created_at"]
    datetime.datetime.fromisoformat(body["updated_at"])
    # Nothing has been parsed yet: record counters are "not computed"
    # (null), never fabricated zeros; there are no recorded errors.
    assert body["statistics"] == {
        "total_records": None,
        "valid_records": None,
        "invalid_records": None,
        "created_records": None,
        "updated_records": None,
        "skipped_records": None,
        "error_count": 0,
    }
    assert "source_file_id" not in body
    assert "storage_key" not in response.text


def test_get_returns_stored_statistics_and_real_error_count(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _importer(club_id)
    job_id = _insert_job(club_id=club_id, created_by_user_id=user_id, status="preview_ready")
    with session_scope() as session:
        job = session.get(ImportJob, job_id)
        assert job is not None
        job.total_records = 10
        job.valid_records = 7
        job.invalid_records = 3
        job.created_records = 0
        job.updated_records = 0
        job.skipped_records = 0
        session.commit()
    _add_errors(job_id, [(2, "email", "invalid_email"), (5, None, "missing_row_data")])
    _authenticate_as(user_id)

    body = client.get(f"{_URL}/{job_id}").json()

    assert body["status"] == "preview_ready"
    assert body["statistics"] == {
        "total_records": 10,
        "valid_records": 7,
        "invalid_records": 3,
        "created_records": 0,
        "updated_records": 0,
        "skipped_records": 0,
        "error_count": 2,
    }


def test_get_nonexistent_import_job_is_404(client: TestClient) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))

    response = client.get(f"{_URL}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_get_malformed_import_id_is_422(client: TestClient) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))

    assert client.get(f"{_URL}/not-a-uuid").status_code == 422


def test_get_requires_authentication(client: TestClient) -> None:
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_importer(club_id))

    assert client.get(f"{_URL}/{job_id}").status_code == 401
    assert client.get(f"{_URL}/{job_id}/errors").status_code == 401


def _assert_hidden_like_nonexistent(client: TestClient, job_id: uuid.UUID) -> None:
    hidden = client.get(f"{_URL}/{job_id}")
    missing = client.get(f"{_URL}/{uuid.uuid4()}")
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == missing.json()["error"]["code"]
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]
    hidden_errors = client.get(f"{_URL}/{job_id}/errors")
    assert hidden_errors.status_code == 404
    assert hidden_errors.json()["error"]["code"] == missing.json()["error"]["code"]


def test_other_importer_cannot_get_someone_elses_job(client: TestClient) -> None:
    """User A creates a job; user B holds `membership.import` + `all` in
    the same Club but is neither the creator nor an Administrator."""
    club_id = _make_club()
    user_a = _importer(club_id)
    _authenticate_as(user_a)
    job_id = _upload(client).json()["import_id"]

    _authenticate_as(_importer(club_id))

    _assert_hidden_like_nonexistent(client, job_id)


def test_user_without_permission_gets_404_not_403_for_existing_job(client: TestClient) -> None:
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_importer(club_id))
    _authenticate_as(_make_user())

    _assert_hidden_like_nonexistent(client, job_id)


def test_creator_who_lost_permission_cannot_get_own_job(client: TestClient) -> None:
    club_id = _make_club()
    creator = _make_user()
    job_id = _insert_job(club_id=club_id, created_by_user_id=creator)
    _authenticate_as(creator)

    _assert_hidden_like_nonexistent(client, job_id)


def test_creator_with_non_all_scope_cannot_get_own_job(client: TestClient) -> None:
    club_id = _make_club()
    creator = _make_user()
    _grant_permission(creator, "membership.import", scope_type="own_groups", club_id=club_id)
    job_id = _insert_job(club_id=club_id, created_by_user_id=creator)
    _authenticate_as(creator)

    _assert_hidden_like_nonexistent(client, job_id)


@pytest.mark.parametrize("club_scoped", [True, False])
def test_administrator_of_the_jobs_club_can_get_any_job_in_it(
    client: TestClient, club_scoped: bool
) -> None:
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_importer(club_id))
    _authenticate_as(_admin(club_id if club_scoped else None))

    response = client.get(f"{_URL}/{job_id}")

    assert response.status_code == 200, response.text
    assert client.get(f"{_URL}/{job_id}/errors").status_code == 200


def test_administrator_of_another_club_cannot_get_the_job(client: TestClient) -> None:
    club_a = _make_club()
    club_b = _make_club()
    job_id = _insert_job(club_id=club_b, created_by_user_id=_importer(club_b))
    _authenticate_as(_admin(club_a))

    _assert_hidden_like_nonexistent(client, job_id)


def test_creator_scoped_to_another_club_cannot_get_own_job(client: TestClient) -> None:
    club_a = _make_club()
    club_b = _make_club()
    creator = _importer(club_a)
    job_id = _insert_job(club_id=club_b, created_by_user_id=creator)
    _authenticate_as(creator)

    _assert_hidden_like_nonexistent(client, job_id)


def test_non_system_role_named_admin_is_not_an_administrator(client: TestClient) -> None:
    """Only the canonical system `admin` role counts — a custom role that
    merely carries `membership.import` is not an Administrator, so it only
    reaches jobs its holder created."""
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_importer(club_id))
    user_id = _make_user()
    with session_scope() as session:
        permission = session.execute(
            select(Permission).where(Permission.code == "membership.import")
        ).scalar_one()
        role = Role(code=f"admin-lookalike-{uuid.uuid4().hex[:6]}", name="admin", is_system=False)
        session.add(role)
        session.flush()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        session.add(
            UserRoleAssignment(user_id=user_id, role_id=role.id, scope_type="all", club_id=club_id)
        )
        session.commit()
    _authenticate_as(user_id)

    _assert_hidden_like_nonexistent(client, job_id)


# --- GET errors -------------------------------------------------------------


def test_errors_for_job_without_errors_is_an_empty_page(client: TestClient) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))
    job_id = _upload(client).json()["import_id"]

    response = client.get(f"{_URL}/{job_id}/errors")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [],
        "pagination": {"page": 1, "page_size": 50, "total": 0, "pages": 0},
    }


def test_errors_are_isolated_per_job(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _importer(club_id)
    job_a = _insert_job(club_id=club_id, created_by_user_id=user_id)
    job_b = _insert_job(club_id=club_id, created_by_user_id=user_id)
    _add_errors(job_a, [(1, "email", "a_error_1"), (2, "phone", "a_error_2")])
    _add_errors(job_b, [(1, "last_name", "b_error_1")])
    _authenticate_as(user_id)

    body_a = client.get(f"{_URL}/{job_a}/errors").json()
    body_b = client.get(f"{_URL}/{job_b}/errors").json()

    assert [item["code"] for item in body_a["items"]] == ["a_error_1", "a_error_2"]
    assert body_a["pagination"]["total"] == 2
    assert [item["code"] for item in body_b["items"]] == ["b_error_1"]
    assert body_b["pagination"]["total"] == 1
    assert client.get(f"{_URL}/{job_a}").json()["statistics"]["error_count"] == 2
    assert client.get(f"{_URL}/{job_b}").json()["statistics"]["error_count"] == 1


def test_error_item_shape(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _importer(club_id)
    job_id = _insert_job(club_id=club_id, created_by_user_id=user_id)
    _add_errors(job_id, [(3, "email", "invalid_email")])
    _authenticate_as(user_id)

    item = client.get(f"{_URL}/{job_id}/errors").json()["items"][0]

    assert set(item) == {
        "id",
        "row_number",
        "field",
        "code",
        "message",
        "severity",
        "matched_person_id",
        "created_at",
    }
    assert item["severity"] == "error"
    assert item["matched_person_id"] is None
    assert item["row_number"] == 3
    assert item["field"] == "email"
    assert item["code"] == "invalid_email"
    assert item["message"] == "invalid_email at row 3"


def test_errors_are_paginated_in_source_order(client: TestClient) -> None:
    club_id = _make_club()
    user_id = _importer(club_id)
    job_id = _insert_job(club_id=club_id, created_by_user_id=user_id)
    _add_errors(
        job_id,
        [(5, "email", "e5"), (None, None, "file_level"), (2, "phone", "e2"), (9, None, "e9")],
    )
    _authenticate_as(user_id)

    page1 = client.get(f"{_URL}/{job_id}/errors", params={"page": 1, "page_size": 3}).json()
    page2 = client.get(f"{_URL}/{job_id}/errors", params={"page": 2, "page_size": 3}).json()
    page3 = client.get(f"{_URL}/{job_id}/errors", params={"page": 3, "page_size": 3}).json()

    assert [item["code"] for item in page1["items"]] == ["file_level", "e2", "e5"]
    assert [item["code"] for item in page2["items"]] == ["e9"]
    assert page3["items"] == []
    assert page1["pagination"] == {"page": 1, "page_size": 3, "total": 4, "pages": 2}
    assert page2["pagination"] == {"page": 2, "page_size": 3, "total": 4, "pages": 2}


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}])
def test_errors_pagination_bounds_are_validated(client: TestClient, params: dict) -> None:
    club_id = _make_club()
    user_id = _importer(club_id)
    job_id = _insert_job(club_id=club_id, created_by_user_id=user_id)
    _authenticate_as(user_id)

    assert client.get(f"{_URL}/{job_id}/errors", params=params).status_code == 422


def test_errors_of_other_users_job_are_not_visible(client: TestClient) -> None:
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_importer(club_id))
    _add_errors(job_id, [(1, "email", "secret_error")])
    _authenticate_as(_importer(club_id))

    response = client.get(f"{_URL}/{job_id}/errors")

    assert response.status_code == 404
    assert "secret_error" not in response.text


def test_errors_of_nonexistent_job_is_404(client: TestClient) -> None:
    club_id = _make_club()
    _authenticate_as(_importer(club_id))

    assert client.get(f"{_URL}/{uuid.uuid4()}/errors").status_code == 404


# --- No arbitrary status change endpoint ------------------------------------


@pytest.mark.parametrize("method", ["patch", "put", "delete"])
def test_no_endpoint_changes_import_job_directly(client: TestClient, method: str) -> None:
    club_id = _make_club()
    user_id = _importer(club_id)
    job_id = _insert_job(club_id=club_id, created_by_user_id=user_id)
    _authenticate_as(user_id)

    response = getattr(client, method)(f"{_URL}/{job_id}", headers=_csrf_headers(client))

    assert response.status_code == 405
    with session_scope() as session:
        assert session.get(ImportJob, job_id).status == "uploaded"


# --- Service-level lifecycle transitions ------------------------------------


def _job_status(job_id: uuid.UUID) -> str:
    with session_scope() as session:
        job = session.get(ImportJob, job_id)
        assert job is not None
        return job.status


def test_service_applies_the_full_happy_path() -> None:
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_make_user())

    for new_status in (
        "parsing",
        "validating",
        "preview_ready",
        "approved",
        "applying",
        "completed",
    ):
        with session_scope() as session:
            job = session.get(ImportJob, job_id)
            assert job is not None
            transition_import_job_status(session, job=job, new_status=new_status)
        assert _job_status(job_id) == new_status


@pytest.mark.parametrize(
    "from_status,to_status",
    [("uploaded", "applying"), ("uploaded", "completed"), ("preview_ready", "applying")],
)
def test_service_rejects_invalid_transition_and_leaves_job_unchanged(
    from_status: str, to_status: str
) -> None:
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_make_user(), status=from_status)

    with session_scope() as session:
        job = session.get(ImportJob, job_id)
        assert job is not None
        with pytest.raises(InvalidImportJobStatusTransitionError):
            transition_import_job_status(session, job=job, new_status=to_status)

    assert _job_status(job_id) == from_status


@pytest.mark.parametrize("terminal", ["completed", "partially_completed", "failed", "cancelled"])
def test_service_rejects_any_transition_out_of_terminal_status(terminal: str) -> None:
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_make_user(), status=terminal)

    for target in ("uploaded", "parsing", "failed", "cancelled", "completed"):
        with session_scope() as session:
            job = session.get(ImportJob, job_id)
            assert job is not None
            with pytest.raises(InvalidImportJobStatusTransitionError):
                transition_import_job_status(session, job=job, new_status=target)
        assert _job_status(job_id) == terminal


def test_service_rejects_non_canonical_target_status() -> None:
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_make_user())

    with session_scope() as session:
        job = session.get(ImportJob, job_id)
        assert job is not None
        with pytest.raises(InvalidImportJobStatusError):
            transition_import_job_status(session, job=job, new_status="done")

    assert _job_status(job_id) == "uploaded"


def test_service_validates_against_the_current_persisted_status() -> None:
    """A stale in-memory status never authorizes a transition: the row is
    re-read under lock first."""
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_make_user())

    with session_scope() as stale_session:
        stale_job = stale_session.get(ImportJob, job_id)
        assert stale_job is not None
        stale_session.commit()  # end the read transaction, keep the object

        with session_scope() as other_session:
            job = other_session.get(ImportJob, job_id)
            assert job is not None
            transition_import_job_status(other_session, job=job, new_status="cancelled")

        assert stale_job.status == "uploaded"  # stale in-memory copy
        with pytest.raises(InvalidImportJobStatusTransitionError):
            transition_import_job_status(stale_session, job=stale_job, new_status="parsing")

    assert _job_status(job_id) == "cancelled"


# --- Database constraints ---------------------------------------------------


def test_import_jobs_rejects_non_canonical_values_at_the_database_level() -> None:
    club_id = _make_club()
    user_id = _make_user()
    job_id = _insert_job(club_id=club_id, created_by_user_id=user_id)

    for column, value in (
        ("status", "done"),
        ("status", "UPLOADED"),
        ("source_format", "xls"),
        ("total_records", -1),
        ("skipped_records", -5),
    ):
        with session_scope() as session:
            job = session.get(ImportJob, job_id)
            assert job is not None
            setattr(job, column, value)
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()


def test_import_job_errors_require_an_existing_job_and_positive_row_number() -> None:
    club_id = _make_club()
    job_id = _insert_job(club_id=club_id, created_by_user_id=_make_user())

    with session_scope() as session:
        session.add(
            ImportJobError(import_job_id=uuid.uuid4(), code="x", message="x", severity="error")
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    with session_scope() as session:
        session.add(
            ImportJobError(
                import_job_id=job_id, row_number=0, code="x", message="x", severity="error"
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_import_job_requires_existing_club_user_and_file() -> None:
    club_id = _make_club()
    user_id = _make_user()
    real_file_job = _insert_job(club_id=club_id, created_by_user_id=user_id)
    with session_scope() as session:
        job = session.get(ImportJob, real_file_job)
        assert job is not None
        file_id = job.source_file_id

    for overrides in (
        {"club_id": uuid.uuid4()},
        {"created_by_user_id": uuid.uuid4()},
        {"source_file_id": uuid.uuid4()},
    ):
        values = {
            "club_id": club_id,
            "created_by_user_id": user_id,
            "source_file_id": file_id,
            "source_format": "csv",
            "status": "uploaded",
        }
        values.update(overrides)
        with session_scope() as session:
            session.add(ImportJob(**values))
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()


# --- Permission seed + migration --------------------------------------------


def _admin_has_membership_import_grant() -> bool:
    with session_scope() as session:
        return (
            session.execute(
                select(RolePermission)
                .join(Role, Role.id == RolePermission.role_id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Role.code == "admin", Permission.code == "membership.import")
            ).first()
            is not None
        )


def test_membership_import_is_seeded_and_granted_only_to_admin() -> None:
    with session_scope() as session:
        roles = set(
            session.execute(
                select(Role.code)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.code == "membership.import")
            )
            .scalars()
            .all()
        )
    assert roles == {"admin"}


def _public_tables(database_url: str) -> set[str]:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            return set(
                conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()


def test_migration_creates_both_tables_at_head(database_url: str) -> None:
    assert {"import_jobs", "import_job_errors"} <= _public_tables(database_url)


def test_downgrade_drops_tables_and_removes_only_the_admin_grant(database_url: str) -> None:
    assert _admin_has_membership_import_grant()

    try:
        downgrade = run_alembic("downgrade", _BEFORE_MIGRATION_REVISION, database_url=database_url)
        assert downgrade.returncode == 0, downgrade.stderr

        assert not {"import_jobs", "import_job_errors"} & _public_tables(database_url)
        assert not _admin_has_membership_import_grant()
        with session_scope() as session:
            # Stable catalog data survives downgrade (f85df8d36f12/
            # 75b7574b6e44/c70bcac43032 precedent).
            still_exists = session.execute(
                select(Permission).where(Permission.code == "membership.import")
            ).scalar_one_or_none()
        assert still_exists is not None
    finally:
        upgrade = run_alembic("upgrade", "head", database_url=database_url)
        assert upgrade.returncode == 0, upgrade.stderr

    assert {"import_jobs", "import_job_errors"} <= _public_tables(database_url)
    assert _admin_has_membership_import_grant()
