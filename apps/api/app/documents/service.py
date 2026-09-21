"""Create and read participant Documents + their Files (TH-0117.3 /
Issue #160).

Canonical sources: ADR-0040 §1/§3/§4/§7, docs/05-api/people-api.md §32.

Authorization is not decided here (see package docstring) — by the time
`create_document` is called, the caller (the API router) has already
established that `person_id` is an existing Person the acting user is
authorized to manage documents for.

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
"""

import hashlib
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.db.documents import Document, File
from app.storage.file_storage import FileStorage, FileStorageError

_INITIAL_DOCUMENT_STATUS = "active"


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
        session.rollback()
        try:
            storage.delete(storage_key)
        except FileStorageError:
            pass
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


__all__ = ["create_document", "read_document_content"]
