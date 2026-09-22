"""Event competition document package export (TH-0117.9 / Issue #172;
ADR-0040 §5/§6/§7, docs/05-api/events-api.md §31.5).

Combines pieces that already exist and are explicitly not modified here:

- the Event's current registered participants (`app.events.participation.
  REGISTERED_STATUS`, the same status the self-registration MVP already
  treats as "actively participating" — ADR-0037);
- the persisted `EventDocumentRequirement` rows for the Event
  (`app.db.documents.EventDocumentRequirement`, unchanged);
- the exact same current-version candidate query and pure validity
  evaluator `app.documents.event_requirements`/`app.documents.validity`
  already use (`list_current_documents_for_person_by_type`,
  `document_check_result`) — this module does not reimplement or fork
  that logic, and does not import/modify `app.documents.event_requirements`
  itself (whose one-Person-at-a-time shape this module's batch,
  single-`now` evaluation does not need);
- `FileStorage` (binary reads only — never a direct filesystem access);
- the canonical audit boundary (`app.audit.service.record_audit_event`),
  audited as `document.exported` (ADR-0040 §7), in the same transaction
  as this module's own write (there is no other business mutation here —
  the audit row is the only thing ever persisted by this module).

Consistency (Issue #172 §concurrency): every participant/requirement pair
is evaluated once, against one `now` timestamp captured at the start of
`generate_event_document_package`, and the specific current `Document`
row that made a pair `valid` is captured at that same moment. Reading its
`File` later in the same call always uses that captured row's own
`file_id` — which a `Document` row's other mutations (`revoke_document`/
`update_document_metadata`) never change, and which `replace_document`
never touches on the *old* row either (it only ever inserts a new,
separate row) — so the binary actually read can never belong to a
different version than the one the warning/manifest decision was based
on, without requiring any new locking or snapshot-isolation architecture:
plain Python object identity within one call is sufficient, because the
one fact that must stay consistent (which `File` a given already-
evaluated `Document` row points to) is, by ADR-0040 §1/§4, permanently
fixed at that row's creation.

Authorization is not decided here (mirrors every other module in this
package): by the time `generate_event_document_package` is called, the
caller (the API router) has already established both `event.read` on the
Event and `document.export`.
"""

import io
import json
import mimetypes
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.db.documents import Document, EventDocumentRequirement, File
from app.db.events import Event, EventParticipation
from app.db.identity import Person
from app.documents.queries import list_current_documents_for_person_by_type
from app.documents.validity import document_check_result
from app.events.participation import REGISTERED_STATUS
from app.storage.file_storage import FileStorage

RequirementResult = Literal["valid", "missing", "expired"]

# Zip-member path segments are built exclusively from this module's own
# generated ordinal and from `document_type` (Issue #172 §15/§10) — never
# from a client-supplied filename — but `document_type` is itself an open
# string (ADR-0040 §1) set by whoever created the Document, so it is
# still sanitized before use as a path segment: only a safe, conservative
# charset survives, guaranteeing no `../`, absolute path, backslash, or
# control character ever reaches the archive.
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
_FALLBACK_PATH_SEGMENT = "document"
_FALLBACK_EXTENSION = ".bin"
_MIN_ORDINAL_WIDTH = 3


@dataclass(frozen=True)
class PackageEntry:
    """One participant/requirement pair's evaluated result — carries no
    Person/Document/File UUID or `storage_key` in any field a caller
    would serialize (Issue #172 §incomplete package warning/§manifest);
    `document` is kept only so `_build_zip_archive` can read the right
    `File` for a `valid` result, and is never itself serialized.
    """

    ordinal: int
    display_name: str
    document_type: str
    required: bool
    result: RequirementResult
    document: Optional[Document]


class DocumentPackageIncompleteError(Exception):
    """At least one participant/requirement pair resolved to `missing`
    or `expired` and the caller did not pass `confirm_incomplete=True`
    (Issue #172 §incomplete package warning). Carries every entry (not
    only the incomplete ones) so the API layer does not need a second
    query to build the full picture, though only the `missing`/`expired`
    ones belong in the 409 response body.
    """

    def __init__(self, entries: list[PackageEntry]) -> None:
        super().__init__("Document package is incomplete")
        self.entries = entries


def _display_name(person: Person) -> str:
    parts = [person.last_name, person.first_name]
    if person.middle_name:
        parts.append(person.middle_name)
    return " ".join(parts)


def _list_registered_participants(session: Session, *, event_id: uuid.UUID) -> list[Person]:
    """Deterministic roster order, mirroring `app.people.queries.
    PERSON_DEFAULT_SORT`'s own `last_name`-first convention — the only
    ordering this codebase already treats as canonical for a list of
    Persons. Only `REGISTERED_STATUS` participants (ADR-0037's own
    "actively participating" status; `CANCELLED_STATUS` rows are not
    current participants).
    """
    return list(
        session.execute(
            sa.select(Person)
            .join(EventParticipation, EventParticipation.person_id == Person.id)
            .where(
                EventParticipation.event_id == event_id,
                EventParticipation.registration_status == REGISTERED_STATUS,
            )
            .order_by(Person.last_name, Person.first_name, Person.id)
        )
        .scalars()
        .all()
    )


def _list_requirements(
    session: Session, *, event_id: uuid.UUID
) -> list[EventDocumentRequirement]:
    return list(
        session.execute(
            sa.select(EventDocumentRequirement)
            .where(EventDocumentRequirement.event_id == event_id)
            .order_by(EventDocumentRequirement.document_type)
        )
        .scalars()
        .all()
    )


def _resolve_requirement(
    session: Session, *, person_id: uuid.UUID, document_type: str, now: datetime
) -> tuple[RequirementResult, Optional[Document]]:
    """`valid`/`missing`/`expired` for one participant/document_type
    pair, reusing exactly the two primitives
    `app.documents.event_requirements._evaluate_document_type` itself
    reuses (never reimplemented here): `list_current_documents_for_
    person_by_type` for the candidate current versions, and the pure
    `document_check_result` evaluator for each one's `valid`/`expired`
    verdict (which already maps a current `revoked` status to
    `expired` — ADR-0040 §5's amendment — without this module knowing
    or caring about that mapping itself).

    A Person is not prevented from having more than one independent
    current-version Document of the same `document_type` (ADR-0040 §5;
    same caveat `list_current_documents_for_person_by_type` documents on
    itself). Unlike the plain check (which only needs to know whether
    *any* of them is valid), package export must pick one concrete
    Document to read a binary from when the result is `valid` — done
    here deterministically by `document_group_id`, the stable identifier
    ADR-0040 §4 defines, so the same input state always yields the same
    choice.
    """
    documents = list_current_documents_for_person_by_type(
        session, person_id=person_id, document_type=document_type
    )
    if not documents:
        return "missing", None
    valid_candidates = sorted(
        (
            document
            for document in documents
            if document_check_result(
                status=document.status, expires_at=document.expires_at, now=now
            )
            == "valid"
        ),
        key=lambda document: document.document_group_id,
    )
    if valid_candidates:
        return "valid", valid_candidates[0]
    return "expired", None


def _evaluate_entries(
    session: Session,
    *,
    participants: list[Person],
    requirements: list[EventDocumentRequirement],
    now: datetime,
) -> list[PackageEntry]:
    entries: list[PackageEntry] = []
    for ordinal, person in enumerate(participants, start=1):
        display_name = _display_name(person)
        for requirement in requirements:
            result, document = _resolve_requirement(
                session,
                person_id=person.id,
                document_type=requirement.document_type,
                now=now,
            )
            entries.append(
                PackageEntry(
                    ordinal=ordinal,
                    display_name=display_name,
                    document_type=requirement.document_type,
                    required=requirement.required,
                    result=result,
                    document=document,
                )
            )
    return entries


def _sanitize_path_segment(value: str) -> str:
    sanitized = _UNSAFE_PATH_CHARS.sub("_", value).strip("._-")
    return sanitized[:64] or _FALLBACK_PATH_SEGMENT


def _extension_for_mime_type(mime_type: str) -> str:
    guessed = mimetypes.guess_extension(mime_type)
    return guessed or _FALLBACK_EXTENSION


def _ordinal_width(entries: list[PackageEntry]) -> int:
    max_ordinal = max((entry.ordinal for entry in entries), default=0)
    return max(_MIN_ORDINAL_WIDTH, len(str(max_ordinal)))


def _build_zip_archive(
    session: Session,
    storage: FileStorage,
    *,
    event: Event,
    entries: list[PackageEntry],
    now: datetime,
) -> bytes:
    """Assemble the ZIP fully in memory. Every binary is read through
    `FileStorage` (never the filesystem directly) — a `FileStorageError`
    (e.g. a missing/unreadable object) propagates unchanged, exactly as
    `app.documents.service.read_document_content` already lets one
    propagate for a single download, so no partial ZIP is ever returned
    to the caller.
    """
    width = _ordinal_width(entries)
    manifest_documents: list[dict[str, object]] = []

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            filename: Optional[str] = None
            if entry.result == "valid" and entry.document is not None:
                file_row = session.get(File, entry.document.file_id)
                # RESTRICT FK + ADR-0040 §3: a File referenced by a
                # retained Document version is never absent.
                assert file_row is not None
                content = storage.get(file_row.storage_key)
                folder = str(entry.ordinal).zfill(width)
                base_name = _sanitize_path_segment(entry.document_type)
                extension = _extension_for_mime_type(file_row.mime_type)
                filename = f"participants/{folder}/{base_name}{extension}"
                archive.writestr(filename, content)
            manifest_documents.append(
                {
                    "participant_ordinal": entry.ordinal,
                    "participant_display_name": entry.display_name,
                    "document_type": entry.document_type,
                    "required": entry.required,
                    "result": entry.result,
                    "filename": filename,
                }
            )

        manifest = {
            "generated_at": now.isoformat(),
            "event_display_name": event.title,
            "event_date": event.start_at.isoformat(),
            "documents": manifest_documents,
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return buffer.getvalue()


def generate_event_document_package(
    session: Session,
    storage: FileStorage,
    *,
    event: Event,
    actor_user_id: uuid.UUID,
    confirm_incomplete: bool,
    request_id: Optional[str] = None,
) -> bytes:
    """Build the synchronous competition document package ZIP for
    `event` (Issue #172). Raises `DocumentPackageIncompleteError`,
    touching neither `FileStorage` nor the database, if any participant/
    requirement pair is `missing`/`expired` and `confirm_incomplete` is
    not `True`. Otherwise builds the archive (any `FileStorageError`
    propagates unchanged, before any audit row is written), then records
    `document.exported` and commits in one transaction — mirroring
    `app.documents.service`'s own business-mutation-plus-audit shape,
    except the "mutation" here is the audit row itself; there is nothing
    else to persist.
    """
    now = datetime.now(timezone.utc)
    participants = _list_registered_participants(session, event_id=event.id)
    requirements = _list_requirements(session, event_id=event.id)
    entries = _evaluate_entries(
        session, participants=participants, requirements=requirements, now=now
    )

    incomplete_entries = [entry for entry in entries if entry.result in ("missing", "expired")]
    if incomplete_entries and not confirm_incomplete:
        raise DocumentPackageIncompleteError(entries=entries)

    archive_bytes = _build_zip_archive(session, storage, event=event, entries=entries, now=now)

    included_count = sum(1 for entry in entries if entry.result == "valid")
    try:
        record_audit_event(
            session,
            action="document.exported",
            actor_type="user",
            actor_user_id=actor_user_id,
            resource_type="event",
            resource_id=event.id,
            outcome="success",
            request_id=request_id,
            # Never document content/medical data/storage_key/filesystem
            # path/internal IDs (Issue #172 §audit) — only aggregate
            # counters already implied by the successful export itself.
            details={
                "participant_count": len(participants),
                "requirement_count": len(requirements),
                "document_count_included": included_count,
                "confirm_incomplete": confirm_incomplete,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return archive_bytes


__all__ = [
    "PackageEntry",
    "DocumentPackageIncompleteError",
    "generate_event_document_package",
]
