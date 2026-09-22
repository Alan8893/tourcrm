"""Create, replace, revoke, update metadata of, and read participant
Documents + their Files (TH-0117.3 / Issue #160; TH-0117.6 / Issue #166
adds `replace_document`; TH-0117.7 / Issue #168 adds `revoke_document`;
TH-0117.8 / Issue #170 adds `update_document_metadata`).

Canonical sources: ADR-0040 §1/§3/§4/§7, docs/05-api/people-api.md §32.

Authorization is not decided here (see package docstring) — by the time
`create_document`/`replace_document` is called, the caller (the API
router) has already established that `person_id` is an existing Person
the acting user is authorized to manage documents for.

Transaction/fail-closed shape, adapted from app.people.service's own
documented pattern for a business mutation combined with an audit record
(ADR-0024 §5) — extended one step further here because a Document's
binary content lives outside PostgreSQL (ADR-0040 §3) and there is no
distributed transaction spanning the two:

    storage.put(storage_key, content)      # outside any DB transaction
    try:
        BEGIN
          File row (session.add/flush)
          Document row (session.add/flush)
          record_audit_event(...) (session.add/flush, no commit)
        COMMIT
    except Exception:
        ROLLBACK
        storage.delete(storage_key)        # best-effort: undo the orphaned
                                            # binary so a failed create never
                                            # leaks storage, on top of never
                                            # leaving committed DB metadata
                                            # pointing at a binary that was
                                            # never stored (§3 requirement)
        raise

Storing the binary *before* opening the database transaction means a
`FileStorage` failure (disk full, an already-taken key — astronomically
unlikely given a freshly generated UUID, but not assumed impossible)
never touches the database at all: there is nothing to roll back. Only
the reverse case — the binary is safely stored but the subsequent
database work fails — needs cleanup, and it is bounded to a single
`storage.delete` call on the one key this same call just created.
`replace_document` follows this identical shape, plus the additional
`uq_documents_group_id_version_number` concurrency backstop documented
on `NotCurrentVersionError` below — no new locking or transaction
architecture is introduced for it.

`revoke_document` never touches `FileStorage` at all (ADR-0040 §4: revoke
is a Document lifecycle mutation in place, not a storage operation) and
has no orphaned-binary case to clean up. Its own concurrency backstop —
combining the current-version check and the write into a single atomic
UPDATE rather than `replace_document`'s separate check-then-insert — is
documented on `revoke_document` itself. `update_document_metadata`
reuses this identical atomic-UPDATE concurrency backstop and likewise
never touches `FileStorage`.
"""

import hashlib
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.db.documents import Document, File
from app.documents.queries import get_current_document_version
from app.storage.file_storage import FileStorage, FileStorageError

_INITIAL_DOCUMENT_STATUS = "active"
_REVOKED_DOCUMENT_STATUS = "revoked"
_VERSION_UNIQUE_CONSTRAINT = "uq_documents_group_id_version_number"
# Issue #170 §request: only these two non-file fields are accepted by
# `update_document_metadata` — never `document_type`/`person_id`/
# `document_group_id`/`version_number`/`status`/`file_id`.
_UPDATABLE_DOCUMENT_METADATA_FIELDS = frozenset({"issued_at", "expires_at"})


class DocumentLifecycleError(Exception):
    """Base class for this module's typed, expected failures — shared by
    `replace_document` and `revoke_document` (both are Document lifecycle
    mutations, ADR-0040 §4)."""


class NotCurrentVersionError(DocumentLifecycleError):
    """`document` is not the current version of its `document_group_id`
    (ADR-0040 §4) — both replacement and revocation are only ever allowed
    on the current version (Issue #166 §3, Issue #168 §current-version
    rule). Also raised when a concurrent mutation wins the race and
    creates a newer version between this call's initial check and its
    write: from the caller's perspective the two cases are
    indistinguishable — by the time the write was attempted, `document`
    was not the current version — so both map to the same typed error and
    the same existence-hiding response at the API layer.
    """

    def __init__(self, *, document_id: uuid.UUID) -> None:
        super().__init__(f"Document {document_id} is not the current version of its group")
        self.document_id = document_id


class AlreadyRevokedError(DocumentLifecycleError):
    """`document`'s current version is already `revoked` — mirrors
    `app.people.guardian_service.terminate_guardian_relationship`'s own
    already-terminal precedent (ADR-0025 §3), the same precedent ADR-0040
    §4 itself cites for Document `revoked`: `revoked` is a terminal
    stored state, so revoking it again is rejected rather than silently
    treated as a no-op success.
    """

    def __init__(self, *, document_id: uuid.UUID) -> None:
        super().__init__(f"Document {document_id} is already revoked")
        self.document_id = document_id


class InvalidDocumentDatesError(DocumentLifecycleError):
    """The *resulting* `issued_at`/`expires_at` — after merging
    `update_document_metadata`'s supplied fields onto the document's
    existing stored values — would have `expires_at` before
    `issued_at` (Issue #170 §date validation). Distinct from
    `replace_person_document`'s own inline router-level check (Issue
    #166), which validates a wholly new pair of dates supplied together
    and has no existing stored state to merge against.
    """


def _is_version_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name == _VERSION_UNIQUE_CONSTRAINT


def _rollback_and_cleanup_storage(session: Session, storage: FileStorage, storage_key: str) -> None:
    session.rollback()
    try:
        storage.delete(storage_key)
    except FileStorageError:
        pass


def _storage_key_for(person_id: uuid.UUID, file_id: uuid.UUID) -> str:
    """Server-generated only (ADR-0040 §3) — a client never supplies or
    influences `storage_key`. Mirrors the ADR's own illustrative layout
    (`root/documents/person/<opaque-key>`, app.storage.local's module
    docstring) without exposing this value in any API response.
    """
    return f"documents/person/{person_id}/{file_id}"


def create_document(
    session: Session,
    storage: FileStorage,
    *,
    person_id: uuid.UUID,
    document_type: str,
    content: bytes,
    original_name: str,
    mime_type: str,
    issued_at: datetime | None,
    expires_at: datetime | None,
    actor_user_id: uuid.UUID,
    request_id: str | None = None,
) -> Document:
    """Create the first version of a new logical document (`version_number
    = 1`, a fresh `document_group_id`) and its backing `File`, auditing the
    mutation as `document.created` (ADR-0040 §7).

    `checksum`/`size_bytes` are always computed from `content` itself —
    never accepted from the caller — so they can never describe anything
    other than the bytes actually stored (Issue #160 §6).
    """
    file_id = uuid.uuid4()
    storage_key = _storage_key_for(person_id, file_id)
    checksum = hashlib.sha256(content).hexdigest()

    storage.put(storage_key, content)
    try:
        file_row = File(
            id=file_id,
            storage_key=storage_key,
            original_name=original_name,
            mime_type=mime_type,
            size_bytes=len(content),
            checksum=checksum,
            storage_backend=storage.backend_name,
            created_by=actor_user_id,
        )
        session.add(file_row)
        session.flush()

        document_id = uuid.uuid4()
        document = Document(
            id=document_id,
            person_id=person_id,
            # ADR-0040 §4: the first version's own id is the stable
            # document_group_id every future replacement copies forward.
            document_group_id=document_id,
            version_number=1,
            document_type=document_type,
            status=_INITIAL_DOCUMENT_STATUS,
            issued_at=issued_at,
            expires_at=expires_at,
            file_id=file_row.id,
            uploaded_by=actor_user_id,
        )
        session.add(document)
        session.flush()

        record_audit_event(
            session,
            action="document.created",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="document",
            resource_id=document.id,
            outcome="success",
            request_id=request_id,
            # Never storage_key/filesystem path/binary/medical content
            # (Issue #160 §2) — only correlation identifiers already
            # visible to an authorized document.read caller anyway.
            details={"document_type": document_type, "file_id": str(file_row.id)},
        )
        session.commit()
    except Exception:
        _rollback_and_cleanup_storage(session, storage, storage_key)
        raise
    return document


def replace_document(
    session: Session,
    storage: FileStorage,
    *,
    document: Document,
    person_id: uuid.UUID,
    content: bytes,
    original_name: str,
    mime_type: str,
    issued_at: datetime | None,
    expires_at: datetime | None,
    actor_user_id: uuid.UUID,
    request_id: str | None = None,
) -> Document:
    """Create a new version of `document`'s logical document (`version_
    number = current + 1`, same `document_group_id`/`document_type`/
    `person_id`) with a new `File`, auditing the mutation as
    `document.replaced` (ADR-0040 §7). The new version's `status` is
    always `active`, regardless of the replaced version's own status
    (Issue #166 §3).

    Raises `NotCurrentVersionError`, touching neither storage nor the
    database, if `document` is not the current version of its group —
    a historical version can never be replaced (ADR-0040 §4). The old
    `File`/`Document` row are never mutated or deleted; only a new pair
    is created, mirroring `create_document`'s own storage-before-database
    ordering and cleanup-on-failure shape (see module docstring).
    """
    current = get_current_document_version(session, document_group_id=document.document_group_id)
    if current.id != document.id:
        raise NotCurrentVersionError(document_id=document.id)

    file_id = uuid.uuid4()
    storage_key = _storage_key_for(person_id, file_id)
    checksum = hashlib.sha256(content).hexdigest()
    new_version_number = current.version_number + 1

    storage.put(storage_key, content)
    try:
        file_row = File(
            id=file_id,
            storage_key=storage_key,
            original_name=original_name,
            mime_type=mime_type,
            size_bytes=len(content),
            checksum=checksum,
            storage_backend=storage.backend_name,
            created_by=actor_user_id,
        )
        session.add(file_row)
        session.flush()

        new_document = Document(
            person_id=person_id,
            document_group_id=current.document_group_id,
            version_number=new_version_number,
            document_type=current.document_type,
            status=_INITIAL_DOCUMENT_STATUS,
            issued_at=issued_at,
            expires_at=expires_at,
            file_id=file_row.id,
            uploaded_by=actor_user_id,
        )
        session.add(new_document)
        session.flush()

        record_audit_event(
            session,
            action="document.replaced",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="document",
            resource_id=new_document.id,
            outcome="success",
            request_id=request_id,
            # Never storage_key/filesystem path/binary/medical content
            # (Issue #166 §9) — only correlation identifiers already
            # visible to an authorized document.read/manage caller anyway.
            details={
                "document_type": current.document_type,
                "file_id": str(file_row.id),
                "previous_document_id": str(current.id),
                "version_number": new_version_number,
            },
        )
        session.commit()
    except IntegrityError as exc:
        _rollback_and_cleanup_storage(session, storage, storage_key)
        if _is_version_conflict(exc):
            raise NotCurrentVersionError(document_id=document.id) from exc
        raise
    except Exception:
        _rollback_and_cleanup_storage(session, storage, storage_key)
        raise
    return new_document


def revoke_document(
    session: Session,
    *,
    document: Document,
    actor_user_id: uuid.UUID,
    request_id: str | None = None,
) -> Document:
    """Revoke the current version of `document`'s logical document group
    in place (ADR-0040 §4): sets `status = 'revoked'` on that one row.
    `Document.id`/`file_id`/`document_group_id`/`version_number` are all
    left unchanged, no new Document version or File is created, and
    `FileStorage` is never called — revoke is strictly a Document
    lifecycle mutation, not a storage operation.

    The current-version check and the write are combined into a single
    atomic `UPDATE ... WHERE version_number = (SELECT MAX(...))` rather
    than `replace_document`'s separate check-then-insert (Issue #168
    §concurrency): a plain check-then-write here would leave a window in
    which a concurrent `replace_document` call creates a newer version
    between this call's check and its write, letting a stale check
    authorize revoking a document that is no longer current by the time
    the write actually happens. Combining them into one statement means
    the "is `document` still current" condition is re-evaluated against a
    fresh snapshot at write time, so the UPDATE can only ever succeed
    against whatever version is genuinely current at that instant — this
    reuses the same `MAX(version_number)` current-version definition
    `get_current_document_version` already expresses (no new locking
    architecture), made atomic with its own write via a single statement
    instead of a separate read.

    Raises `NotCurrentVersionError`, mutating nothing, if `document` is
    not the current version of its group (including the checked-then-
    write race above) — a historical version can never be revoked
    (ADR-0040 §4), the same rule `replace_document` enforces for
    replacement. Raises `AlreadyRevokedError`, also mutating nothing, if
    the current version's stored status is already `revoked`.
    """
    current_version_number = (
        sa.select(sa.func.max(Document.version_number))
        .where(Document.document_group_id == document.document_group_id)
        .scalar_subquery()
    )
    result = session.execute(
        sa.update(Document)
        .where(
            Document.id == document.id,
            Document.version_number == current_version_number,
            Document.status != _REVOKED_DOCUMENT_STATUS,
        )
        .values(status=_REVOKED_DOCUMENT_STATUS, updated_at=sa.func.now())
    )
    if result.rowcount == 0:
        current = get_current_document_version(
            session, document_group_id=document.document_group_id
        )
        if current.id != document.id:
            raise NotCurrentVersionError(document_id=document.id)
        raise AlreadyRevokedError(document_id=document.id)

    session.refresh(document)
    try:
        record_audit_event(
            session,
            action="document.revoked",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="document",
            resource_id=document.id,
            outcome="success",
            request_id=request_id,
            # Never storage_key/filesystem path/binary/medical content
            # (Issue #168 §audit) — only correlation identifiers already
            # visible to an authorized document.read/manage caller anyway.
            details={"document_type": document.document_type, "file_id": str(document.file_id)},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return document


def update_document_metadata(
    session: Session,
    *,
    document: Document,
    actor_user_id: uuid.UUID,
    request_id: str | None = None,
    **fields: datetime | None,
) -> Document:
    """Update only the supplied non-file metadata fields of `document`'s
    current version in place (ADR-0040 §4/§7, Issue #170). Only
    `issued_at`/`expires_at` are ever accepted (`_UPDATABLE_DOCUMENT_
    METADATA_FIELDS`) — `document_type`/`person_id`/`document_group_id`/
    `version_number`/`status`/`file_id` are never touched. Creates no
    new Document version, no new File, and never calls `FileStorage` —
    a plain, in-place, DB-only metadata correction, distinct from
    `replace_document` (new file/version) and `revoke_document`
    (lifecycle status transition).

    `fields` must be exactly the fields the caller actually supplied
    (the API layer's `exclude_unset=True`, Issue #170 §request) — an
    absent field leaves the document's current value untouched, while
    an explicit `None` present in `fields` clears it; `{}` and
    `{"expires_at": None}` are different requests, and the caller (not
    this function) is responsible for rejecting the empty-`fields` case
    as invalid, since that check needs no document state at all.

    Raises `InvalidDocumentDatesError`, mutating nothing, if the
    *resulting* `issued_at`/`expires_at` — after merging `fields` onto
    `document`'s current stored values — would have `expires_at` before
    `issued_at`; this validates the merged result, not just the
    supplied fields, so a PATCH of only one field is checked against
    the other field's existing stored value too (Issue #170 §date
    validation).

    The current-version check and the write are combined into a single
    atomic UPDATE, the same pattern `revoke_document` uses and for the
    same reason (Issue #170 §concurrency): a plain check-then-write
    would leave a window in which a concurrent `replace_document` call
    creates a newer version between this call's check and its write,
    letting a stale check authorize updating a document that is no
    longer current by the time the write actually happens. Raises
    `NotCurrentVersionError`, mutating nothing, if `document` is not the
    current version of its group (including that race) — a historical
    version can never be updated (ADR-0040 §4), the same rule
    `replace_document`/`revoke_document` enforce.
    """
    unknown_fields = set(fields) - _UPDATABLE_DOCUMENT_METADATA_FIELDS
    if unknown_fields:
        raise ValueError(
            f"Fields not updatable via update_document_metadata: {sorted(unknown_fields)}"
        )
    if not fields:
        raise ValueError("update_document_metadata requires at least one field")

    resulting_issued_at = fields["issued_at"] if "issued_at" in fields else document.issued_at
    resulting_expires_at = fields["expires_at"] if "expires_at" in fields else document.expires_at
    if (
        resulting_issued_at is not None
        and resulting_expires_at is not None
        and resulting_expires_at < resulting_issued_at
    ):
        raise InvalidDocumentDatesError("expires_at must not be before issued_at")

    current_version_number = (
        sa.select(sa.func.max(Document.version_number))
        .where(Document.document_group_id == document.document_group_id)
        .scalar_subquery()
    )
    result = session.execute(
        sa.update(Document)
        .where(
            Document.id == document.id,
            Document.version_number == current_version_number,
        )
        .values(**fields, updated_at=sa.func.now())
    )
    if result.rowcount == 0:
        raise NotCurrentVersionError(document_id=document.id)

    session.refresh(document)
    try:
        record_audit_event(
            session,
            action="document.updated",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="document",
            resource_id=document.id,
            outcome="success",
            request_id=request_id,
            # Never storage_key/filesystem path/binary/medical content
            # (Issue #170 §audit) — only the names of the fields changed
            # and the document_type, already visible to an authorized
            # document.read/manage caller anyway.
            details={"document_type": document.document_type, "fields_updated": sorted(fields)},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return document


def read_document_content(
    session: Session,
    storage: FileStorage,
    *,
    document: Document,
    file: File,
    actor_user_id: uuid.UUID,
    request_id: str | None = None,
) -> bytes:
    """Read `document`'s binary content through the `FileStorage` port and
    record the audit-required `document.downloaded` event (ADR-0040 §7) —
    every document type, not only `medical_certificate`; the ADR draws no
    such exception.

    Content is read *before* the audit record is written and committed:
    if `storage.get()` fails (e.g. the backing object is unexpectedly
    missing), no `document.downloaded` row is ever written for a download
    that did not actually happen, and the exception propagates unchanged.
    There is no business mutation in this transaction to roll back on an
    audit-insert failure (unlike `create_document`) — the audit row is the
    only pending change, and it is simply never committed.
    """
    content = storage.get(file.storage_key)
    record_audit_event(
        session,
        action="document.downloaded",
        actor_type="user",
        actor_user_id=actor_user_id,
        resource_type="document",
        resource_id=document.id,
        outcome="success",
        request_id=request_id,
        details={"document_type": document.document_type},
    )
    session.commit()
    return content


__all__ = [
    "DocumentLifecycleError",
    "NotCurrentVersionError",
    "AlreadyRevokedError",
    "InvalidDocumentDatesError",
    "create_document",
    "replace_document",
    "revoke_document",
    "update_document_metadata",
    "read_document_content",
]
