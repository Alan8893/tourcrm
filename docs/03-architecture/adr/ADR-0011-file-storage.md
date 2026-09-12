# ADR-0011 — File and object storage

## Status

Accepted.

## Context

TourCRM will store participant photos, signed/scan documents, GPX files, knowledge-base attachments, event media and potentially generated reports. Binary content should not become tightly coupled to PostgreSQL.

## Decision

Use an object/file storage abstraction for binary assets and keep only metadata and references in PostgreSQL.

The domain layer must not depend directly on a particular storage vendor or filesystem path. It uses a storage service abstraction with operations equivalent to:

- create/upload object;
- obtain controlled download/read access;
- replace/version object where supported;
- delete/archive object according to retention policy;
- inspect metadata;
- integrity verification.

The initial deployment may use local filesystem-backed storage on the application server or a self-hosted S3-compatible object store, provided the abstraction remains unchanged. The concrete implementation must be selected in an infrastructure implementation decision before release.

Metadata stored in PostgreSQL should include at minimum:

- object identifier;
- storage key;
- logical file name;
- MIME type;
- size;
- checksum/integrity metadata;
- owner/domain relation;
- created_at;
- created_by;
- lifecycle/status;
- optional retention/expiration metadata.

Protected files must never be exposed through an unauthenticated static directory merely because they exist on disk.

## Rationale

The club needs controlled access to sensitive files and future portability from a single local server to another storage backend or VPS.

## Consequences

- DB backups and file backups are separate concerns and both are required.
- File authorization must be enforced using the domain permissions of the referenced object.
- Generated/downloaded files may use temporary or signed access mechanisms where appropriate.
- Orphan detection and cleanup must be implemented operationally.
