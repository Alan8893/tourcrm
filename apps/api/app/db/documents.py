"""Participant Document/File persistence foundation (TH-0117.1 / Issue #156).

Canonical source: docs/03-architecture/adr/ADR-0040-document-domain-and-
file-storage.md, which this module follows field-for-field. See also
docs/03-architecture/database-schema.md §11 (`files`), §15.1 (`documents`),
§15.2 (`event_document_requirements`), and docs/03-architecture/
domain-model.md §22.

`File` and `Document` are deliberately separate entities (ADR-0040 §1):
`File` is immutable physical artifact metadata — once created, a row is
never mutated and its binary content is never replaced in place; `Document`
is the versioned business/legal record about a participant document,
explicitly owned by a `Person` (ADR-0040 §2 — no polymorphic `subject_type`/
`subject_id`, per the existing ADR-0016 requirement for security-sensitive
documents). Replacing a document's file creates a new `File` row and a new
`Document` version; it never overwrites either.

This module is persistence foundation only, matching the shape of
app.db.groups/app.db.events (plain FK columns, no ORM `relationship()`
objects, no back-populates on `Person`/`User`/`Event` — those classes are
not modified by this module). Non-goals here (Issue #156 §7): FileStorage
implementation/adapter, upload/download endpoints, a Document/
EventDocumentRequirement API, a validity/check engine, and export/package
generation. Enforcing that an existing `Document` version's `file_id`/
`version_number`/`document_group_id` are never mutated after creation is a
future application/service-layer concern (mirroring how
app.db.groups.GroupInstructorAssignment's own docstring explains that
Club-ownership integrity is a service-layer invariant, not a database
trigger) — constructing/updating rows directly through this module's ORM
classes does not enforce it.
"""

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ADR-0040 §4: closed, three-value lifecycle vocabulary. `missing` is never
# a member of this set — it is only ever the result of an
# EventDocumentRequirement check (see EventDocumentRequirement's own
# docstring), never a persisted Document.status value. This ADR explicitly
# leaves the check's result undefined for a `revoked` current version and
# does not fold `revoked` into `expired` — neither this vocabulary nor any
# code in this module encodes such a mapping.
CANONICAL_DOCUMENT_STATUSES: frozenset[str] = frozenset({"active", "expired", "revoked"})

_DOCUMENT_STATUS_VALUES = ",".join(f"'{value}'" for value in CANONICAL_DOCUMENT_STATUSES)


class File(Base):
    """Immutable physical file artifact metadata (ADR-0040 §1;
    database-schema.md §11 `files`).

    Binary content is stored outside PostgreSQL (ADR-0006/ADR-0011/
    ADR-0040 §3) — this table holds metadata only, and this module does not
    implement the `FileStorage` port or any storage adapter (Issue #156
    §7). `storage_key` is an opaque reference into that external storage,
    never a public URL — access control and the actual read/write path are
    later, separate work.

    Domain-neutral and shared: this is the same canonical entity
    `route_files` already references (database-schema.md §11) and the
    eventual target of `Person.photo_file_id` once a later implementation
    task adds that FK (ADR-0040 explicitly defers that FK addition; it is
    not added by this module — see `Person.photo_file_id`'s own docstring
    in app.db.identity).
    """

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_key: Mapped[str] = mapped_column(sa.String(512), nullable=False, unique=True)
    original_name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    # ADR-0040 §3: an open string (e.g. "local"/"s3") — no closed
    # vocabulary is defined by the ADR, and none is invented here.
    storage_backend: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    # Nullable FK per the general cross-cutting-columns convention
    # (database-schema.md §4); actor references target User, matching the
    # actor-references-User pattern used elsewhere (e.g. Event.created_by,
    # AuditLog.actor_user_id).
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )


class Document(Base):
    """A versioned participant document (ADR-0040 §1/§2/§4;
    database-schema.md §15.1 `documents`).

    Explicitly owned by a `Person` via `person_id` — never a polymorphic
    `subject_type`/`subject_id` pair (ADR-0040 §2). `document_type` is an
    open string; TH-0117 requires at least `medical_certificate` to exist
    and behave per this module's lifecycle rules, and no closed vocabulary
    or additional medical-specific type is invented here.

    Version identity (ADR-0040 §4): `document_group_id` is a stable value
    shared by every version of the same logical document — the first
    version's own `id` serves as this value; a replacement copies it
    forward. `version_number` starts at 1 and strictly increases within a
    `document_group_id`. The **current** version of a logical document is
    the row with `MAX(version_number)` for its `document_group_id` — no
    separate "is current" flag is stored, to avoid a flag that could drift
    out of sync. Replacing a document's file creates a new row (new
    `file_id`, incremented `version_number`, same `document_group_id`)
    rather than mutating the prior version in place; `revoked` is applied
    to the current version in place and does not create a new version.

    `status` is restricted by `ck_documents_status_valid` to exactly
    `active`/`expired`/`revoked` (see `CANONICAL_DOCUMENT_STATUSES` above).
    `missing` is never a valid value here — see that constant's own
    docstring. No `revoked -> expired` mapping is encoded anywhere in this
    module (ADR-0040 §5 leaves that case explicitly undefined).
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False
    )
    # Not a foreign key: this value is a shared identifier across sibling
    # version rows (equal to the first version's own `id`), not a reference
    # to a separate "document group" table — ADR-0040 §4 does not define
    # one.
    document_group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    document_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    issued_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    # RESTRICT: a File still referenced by retained Document history must
    # not be deletable out from under it (ADR-0040 §3).
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="RESTRICT"), nullable=False
    )
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.CheckConstraint(
            f"status IN ({_DOCUMENT_STATUS_VALUES})",
            name="ck_documents_status_valid",
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_documents_version_number_positive",
        ),
        # Same "later timestamp >= earlier timestamp" idiom already used
        # throughout this codebase (e.g. ClubMembership.left_at,
        # GuardianRelationship.valid_to).
        sa.CheckConstraint(
            "issued_at IS NULL OR expires_at IS NULL OR expires_at >= issued_at",
            name="ck_documents_expires_at_after_issued_at",
        ),
        # ADR-0040 §4: at most one row per version number within a logical
        # document's lineage.
        sa.UniqueConstraint(
            "document_group_id", "version_number", name="uq_documents_group_id_version_number"
        ),
        sa.Index("ix_documents_person_id", "person_id"),
        sa.Index("ix_documents_document_group_id", "document_group_id"),
        sa.Index("ix_documents_file_id", "file_id"),
    )


class EventDocumentRequirement(Base):
    """An Event's declared requirement for a participant document type
    (ADR-0040 §5; database-schema.md §15.2 `event_document_requirements`).

    Deliberately does **not** associate `Document` directly with `Event`
    (ADR-0040 §5) — this is the only bridge between the two, and it
    expresses a requirement declaration only, never a specific Document
    reference. Checking a requirement against a participant's documents
    (producing `valid`/`missing`/`expired`) is a read-only computation, not
    a persisted row, and is not implemented by this module (Issue #156 §7:
    no validity/check engine in this task).

    No `created_at`/`updated_at`: ADR-0040 §5 and Issue #156's own field
    list for this entity are exactly `id`/`event_id`/`document_type`/
    `required` — the same deliberate omission already used for `Role`/
    `Permission` (database-schema.md §6.1/§6.2), whose docstrings explain
    this is a considered choice for a short, closed-field catalog/
    declaration row, not an oversight.
    """

    __tablename__ = "event_document_requirements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    )
    document_type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    required: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint(
            "event_id", "document_type", name="uq_event_document_requirements_event_id_type"
        ),
        sa.Index("ix_event_document_requirements_event_id", "event_id"),
    )


__all__ = [
    "CANONICAL_DOCUMENT_STATUSES",
    "File",
    "Document",
    "EventDocumentRequirement",
]
