# ADR-0040: Document Domain and File Storage

**Status:** Accepted
**Date:** 2026-09-21
**Decision type:** PO Decision
**Scope:** People Management / Events / Storage Infrastructure

This ADR is a documentation-only architecture baseline (TH-0117.0 / Issue #154). It does not implement any backend/frontend code, SQLAlchemy model, or Alembic migration — see "Non-decisions / Out of scope" below.

## Context

TourCRM has needed a concrete Document/File contract since early planning: ADR-0006 and ADR-0011 already establish the general principle that binary content lives outside PostgreSQL behind a storage abstraction, and ADR-0016 already requires that a security-sensitive document use an explicit, non-polymorphic association rather than a bare `subject_type`/`subject_id` pair. None of the three, however, defines a concrete schema, a concrete storage port, or how a *participant* document (medical certificate, insurance, waiver, etc.) is created, versioned, checked for validity, or connected to an Event's document requirements.

Three pieces of prior, unreconciled draft material already exist and must not be silently ignored:

- `database-schema.md` §11 already sketches a generic `files` table (`id`, `storage_key`, `original_name`, `mime_type`, `size_bytes`, `checksum`, `storage_backend`, `created_by`, timestamps) — this is, field-for-field, the File entity this ADR formalizes; no new File table shape is invented.
- `database-schema.md` §15 already sketches a generic `documents` table using a polymorphic `subject_type`/`subject_id` pair, plus a `consents` table, plus `qualifications.document_id`. This is exactly the shape ADR-0016 already says must not be the *sole* ownership mechanism for a security-sensitive document.
- `apps/api/app/db/identity.py`'s `Person.photo_file_id` already exists in code today as a plain nullable UUID column, explicitly commented in the codebase as having no FK because "no `files` table exists yet in this codebase" — i.e. the implementation is already anticipating this exact ADR.

TH-0117 (participant documents — medical certificates and similar records tied to a Person, checked against Event requirements before a competition/trip) is the first concrete consumer of this domain and forces the outstanding questions to be resolved now, before any implementation code is written.

This ADR resolves those questions for the **participant-document slice** specifically. It does not redesign the broader, aspirational document/consent module described in `docs/04-modules/documents-and-consents.md` (DocumentType registry, confidentiality-level enum, `StorageObject` terminology, Consent-document linkage, retention configuration UI) — that document describes a longer-term vision and is not contradicted by this ADR, but this ADR is the narrower, concrete, implementation-ready contract for the slice TH-0117 actually needs. Where the two differ in terminology or shape (e.g. `StorageObject` vs. `File`, generic `owner_type/owner_id` vs. explicit `person_id`), this ADR is authoritative for the participant-document slice; reconciling the rest of that module document is separate future work.

## Decision

### 1. File vs Document

The physical binary artifact and the business/legal record about it are two different entities, never one table:

**`File`** — immutable physical artifact metadata. Once created, a File row is never mutated and its binary content is never replaced in place. Canonical fields (identical to the existing `database-schema.md` §11 draft — no new shape introduced):

- `id`
- `storage_key`
- `original_name`
- `mime_type`
- `size_bytes`
- `checksum`
- `storage_backend`
- `created_by`
- `created_at` / `updated_at`

**`Document`** — the business/legal record about a participant document. Canonical fields:

- `id`
- `person_id`
- `document_type`
- `status`
- `issued_at`
- `expires_at`
- `file_id`
- version/history metadata (§4 below)
- `uploaded_by`
- `created_at` / `updated_at`

Replacing a Document's file **never** overwrites the existing `File` row or its binary content. It creates a new `File` row and a new `Document` version (§4) pointing to it. The old `File` and the old `Document` version remain, unmutated, as history.

`document_type` remains an open string field for this ADR, exactly like `Event.event_type`/`Group.status` before their own closed-vocabulary decisions were made separately. The only concretely required value for TH-0117 is `medical_certificate` (driven by the lifecycle and `EventDocumentRequirement` requirements below). This ADR does not enumerate a closed `document_type` vocabulary and does not invent additional types beyond `medical_certificate` — adding further types during implementation does not require a new ADR unless a type needs distinct lifecycle/sensitivity rules of its own.

### 2. Person association

For TH-0117, a participant Document has an **explicit** association to its owner:

```text
Document.person_id -> Person.id
```

A free polymorphic `subject_type`/`subject_id` pair is **not** used as the ownership mechanism for participant documents. This directly fulfils ADR-0016's existing requirement ("a generic polymorphic subject reference must not be used as the sole ownership mechanism for a security-sensitive document") for the Person case — realized here as a direct FK column rather than a separate per-domain bridge table, because Person is the only owner domain TH-0117 needs and a document simultaneously owned by multiple domains is out of scope for this slice. ADR-0016 itself is not amended: a direct FK is a stricter, non-polymorphic association, so it satisfies rather than contradicts ADR-0016's decision.

Generic document ownership for other domains (Trip, Equipment, Finance, Club-level documents already sketched in `documents-and-consents.md` §10) is explicitly deferred. It is not designed by this ADR and must not be inferred from it.

### 3. Storage abstraction

Business/domain code never depends on filesystem or object-storage details directly. A storage port — named `FileStorage` — is the sole boundary:

- `put(content, metadata) -> File` — stores binary content and returns the created immutable `File` metadata row.
- `get(file)` — returns a readable stream/handle to the binary content for an already-authorized caller; this is never a public URL.
- `exists(file)` — checks whether the backing object is present.
- `revoke` / `delete(file)` — removes or deprecates the backing object according to retention/lifecycle policy. This operation is bounded by the same "historically significant records are not deleted without a documented business basis" rule as the rest of the system (`security-and-privacy.md` §16): it must not silently delete a `File` still referenced by a `Document` version that is part of retained history. Exact retention periods remain an open policy decision (`security-and-privacy.md` §26), unchanged by this ADR.

PostgreSQL stores only `File`/`Document` metadata. Binary content lives outside PostgreSQL. A local filesystem adapter is acceptable for development; production must be able to use a private S3-compatible/object storage backend without any change to domain code — this is the same requirement ADR-0006/ADR-0011 already state, now given a concrete port name (`FileStorage`) and a concrete minimal operation set.

`storage_key` is never a public, guessable, or directly browsable URL. All file access goes through an authorized application endpoint that performs the permission check first (§6) and only then streams/returns content via `FileStorage.get`.

### 4. Document lifecycle and history

Documents are versioned and immutable in history — an upload that replaces content never overwrites a prior version's row or file.

**Version identity.** Each Document row carries:

- `document_group_id` — a stable identifier shared by every version of the same logical document over time (the first version's own `id` serves as this value; a replacement copies it forward).
- `version_number` — a positive integer, starting at 1 for the first version and strictly increasing with every replacement within the same `document_group_id`.

The **current** version of a logical document is the row with the maximum `version_number` for its `document_group_id`. No separate "is current" flag is introduced — a maximum-of-`version_number` query is sufficient and cannot drift out of sync the way a redundant boolean could.

**Lifecycle states.** The canonical, closed set of stored `status` values is:

- `active`
- `expired`
- `revoked`

`revoked` is reached only through an explicit action (mirroring `GuardianRelationship.status = revoked` via `terminate`, ADR-0025 §3) and is applied to the current version in place — revoking a document does not create a new version. Replacing a document's file (§1) does create a new version; correcting non-file metadata on the current version (e.g. fixing a typo'd `expires_at`) updates that version in place and is a plain `document.updated`, not a new version.

Current validity, for `EventDocumentRequirement` checks and any operational display, is computed the same way `GuardianRelationship`'s read-time expiry already is (`people-api.md` §18): from stored `status` together with `expires_at`, never by relying on a background job having already flipped `status` to `expired`. A document whose stored `status` is still `active` but whose `expires_at` has already elapsed is treated as expired at read time. `status = 'expired'` may also be written explicitly (e.g. by an administrative correction), but correct validity determination never depends on that having happened — no background job, scheduler, or worker is introduced by this ADR to perform that transition.

`medical_certificate` documents in particular must retain every historical certificate — replacing an expired or superseded medical certificate creates a new version; the prior one is never deleted or overwritten, only superseded by version ordering.

### 5. EventDocumentRequirement

`Document` is **not** directly associated with `Event`. A separate concept expresses "this Event requires document type X from its participants":

**`EventDocumentRequirement`** — minimal fields:

- `id`
- `event_id`
- `document_type`
- `required` (boolean)

A uniqueness constraint on `(event_id, document_type)` prevents two contradictory requirement rows for the same Event/document-type pair — an ordinary data-integrity constraint, not a new domain concept.

**Validation.** Checking a requirement against a specific participant's documents is a read-only computation (never a persisted row) that produces exactly one of:

- `valid` — the participant has a current-version Document of the required `document_type`, with `status` effectively `active` (§4's read-time rule) at the time of the check.
- `missing` — no Document of the required `document_type` exists for that Person at all. **`missing` is never a persisted `Document.status` value** — it is purely the absence result of this check, exactly as TH-0117 specifies.
- `expired` — a Document of the required type exists, but its current version's stored `status` is `expired`, its `expires_at` has already elapsed (§4's read-time rule), or its current version's stored `status` is `revoked`, at the time of the check.

**Revoked mapping decision (TH-0117.4):** a current Document version with stored `status = 'revoked'` maps to the requirement-check result `expired`. `revoked` remains a distinct persisted Document lifecycle state and is not changed or overwritten by this mapping; the mapping applies only to the derived three-value EventDocumentRequirement result. The requirement-check result set therefore remains exactly `valid` / `missing` / `expired`, with no fourth result value. A revoked document is not treated as `missing` because the Document exists; it is treated as `expired` because the participant does not have a usable current document satisfying the requirement. Historical valid versions do not override the current version's revoked result.

**Competition/event document package export.** Before an export that packages participant documents for an Event (e.g. a competition document package), the exporting user must receive an explicit warning listing any participant/requirement pairs that resolve to `missing` or `expired`. This ADR does not design the export format or UI — only the requirement that the warning must occur before such an export completes.

This ADR does not change existing Event or EventParticipation authorization (ADR-0020, ADR-0023, ADR-0037) in any way. `EventDocumentRequirement` is an additional, independently-authorized concept layered next to those, not a modification of them.

### 6. Permissions and sensitive documents

`person.read` does **not** grant access to Document content. This follows the already-accepted principle in `roles-and-permissions.md` §8: "access to Person does not automatically mean access to all of Person's child objects."

Three dedicated permissions govern the Document domain:

- `document.read` — view Document metadata and derived validity (`valid`/`missing`/`expired`), and download the binary content of an individually authorized Document. It does not grant export/package access.
- `document.manage` — create, replace (new version), and revoke a Document.
- `document.export` — produce a multi-document/export package that includes Document content (e.g. the competition package in §5). It is distinct from downloading one individually authorized Document.

(`document.read`, `document.manage`, and `document.export` are the canonical permission names for this domain. Their concrete role/scope grants must follow the existing role/scope policy; this ADR does not redefine role grants.)

Medical certificates are sensitive documents. Ordinary People/Group views, and an ordinary Group export, must never expose medical-document content or a direct download link to it. An operational workflow (e.g. a roster or an event readiness screen) may show the derived validity (`valid`/`missing`/`expired`) from §5 without exposing the underlying file — the derived status is not itself sensitive content.

No arbitrary medical metadata fields (diagnosis, doctor name, clinic, medical conditions, blood type, or similar) are introduced by this ADR. The `Document`/`File` shape in §1 is the complete field set for TH-0117; any additional medical-specific field requires its own future decision, per `security-and-privacy.md` §3.4's existing rule that no medical field is added "only on a developer's initiative."

### 7. Audit

Document mutations and sensitive access use the existing canonical audit infrastructure (ADR-0024) — no parallel or Document-specific audit mechanism is introduced. This ADR amends ADR-0024 §4's closed action vocabulary with:

```text
document.created
document.updated
document.replaced
document.revoked
document.downloaded
document.exported
```

(See the corresponding ADR-0024 amendment for the full rule text.) The audit-required mutation and its `record_audit_event()` call remain in the same database transaction, fail-closed, exactly as ADR-0024 §5 already requires. Sensitive document download (`document.downloaded`) and export (`document.exported`) are both audit-required — a `document.export` action that includes a medical certificate is not exempt merely because the export also contains non-sensitive documents.

No raw document content, checksum-adjacent binary data, or medical content is ever written into `AuditLog.details` — the existing ADR-0024 §6 secret/safe-payload rule applies unchanged.

## Consequences

- `Person.photo_file_id` gains a concrete target shape to eventually reference (the `files` table this ADR formalizes), but remains an FK-less plain UUID column until a separate implementation task adds the constraint (see "Non-decisions" below).
- `database-schema.md`'s existing `files` table draft (§11) becomes the canonical File entity, shared by participant documents, routes/GPX (`route_files`), and the eventual `Person.photo_file_id` FK — no second, competing file-metadata table is introduced.
- `database-schema.md`'s existing generic `documents` table draft (§15) is superseded, for the participant-document case only, by the explicit-FK, versioned shape in §1/§4 of this ADR; the polymorphic-subject question for other domains (Trip, Equipment, Finance, Club-level documents) remains open and unresolved by this ADR.
- Any future People/Group/Event view or export must be built with the assumption that Document access requires one of the dedicated Document permissions in addition to whatever permission governs the surrounding Person/Group/Event resource. `document.read` covers individually authorized metadata/content access; `document.export` covers document exports/packages. `person.read`/`group.read`/`event.read` alone are never sufficient.
- Implementation following this ADR has completed the participant-document slice: `files`/`documents`/`event_document_requirements` persistence, the `FileStorage` port and local adapter, the three canonical permissions, the ADR-0024 document audit vocabulary, participant document APIs, validity/requirement checks, requirement management, replacement, revoke, metadata update and competition document-package export. The existing `Person.photo_file_id` remains governed by its separate file-domain integration decision.

## Non-decisions / Out of scope

This ADR explicitly does **not**:

- Write any backend or frontend application code.
- Preserve the implemented SQLAlchemy/Alembic persistence and audit vocabulary contract when extending the broader document domain.
- Extend storage only through the existing `FileStorage` port; S3-compatible production infrastructure remains a deployment concern outside this participant-document MVP.
- Implement upload, download, or export handling.
- Add any UI, page, or navigation entry.
- Add a `Person.photo_file_id` foreign key or otherwise modify the `persons` table.
- Introduce OCR, electronic signatures, or automatic/external medical-data integrations.
- Enumerate a closed `document_type` vocabulary beyond requiring `medical_certificate` to exist and behave as described in §4.
- Invent additional medical metadata fields.
- Design the concrete competition/event document package export format or its UI.
- Change existing Event, EventParticipation, or People authorization models.
- Change Navigation Architecture.
- Reconcile every aspect of the broader `docs/04-modules/documents-and-consents.md` vision (DocumentType registry, Consent-document linkage, confidentiality-level enum, retention configuration) — only the participant-document slice needed for TH-0117 is resolved here.
- Concrete role/scope grants for the three Document permissions remain implementation work and are not assigned by this ADR.

## Traceability

- Issue #154 — TH-0117.0: Document domain architecture and canonical documentation
- ADR-0006 — File storage (original storage-abstraction principle)
- ADR-0011 — File and object storage (storage port operations, metadata minimum)
- ADR-0016 — Document ownership and association model (non-polymorphic association requirement for sensitive documents)
- ADR-0020 / ADR-0023 / ADR-0037 — Event authorization/participation contract (unchanged by this ADR)
- ADR-0024 — Canonical audit infrastructure (amended by this ADR — see its own amendment section)
- ADR-0025 §3 — `GuardianRelationship` `terminate`/`revoked` precedent mirrored by Document `revoked`
- `docs/03-architecture/database-schema.md` §11 (`files`), §15 (`documents`/`consents`), §12 (`qualifications.document_id`)
- `docs/03-architecture/domain-model.md` §22 (`Document`)
- `docs/02-requirements/roles-and-permissions.md` §4 (permission catalog), §8 (Person access does not imply child-object access)
- `docs/04-modules/documents-and-consents.md` — broader, not-yet-reconciled long-term module vision
- `apps/api/app/db/identity.py` — existing `Person.photo_file_id` (FK-less, explicitly documented as pending this ADR)
- `docs/05-api/people-api.md`, `docs/05-api/events-api.md`, `docs/05-api/endpoint-inventory.md` — planned API contract sections added by this task
- `docs/07-security/security-and-privacy.md` — planned Document-domain security requirements added by this task
