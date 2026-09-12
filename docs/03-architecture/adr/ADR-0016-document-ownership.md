# ADR-0016: Document ownership and association model

## Status
Accepted

## Context

Documents may be related to persons, trips, events, consents and other domains. A generic polymorphic `subject_type/subject_id` reference does not provide database-level referential integrity.

## Decision

The canonical `documents` table stores file/document metadata only. Domain ownership is represented through explicit association tables for high-value and security-sensitive relationships.

Required associations include dedicated relations for at least:

- person documents;
- consent evidence;
- event documents;
- trip documents;
- equipment/finance documents where applicable.

A generic polymorphic subject reference must not be used as the sole ownership mechanism for a security-sensitive document.

A document may have multiple legitimate associations when the same immutable file/document is relevant to multiple domains; associations are auditable independently.

## Consequences

PostgreSQL foreign keys can enforce ownership relationships. Authorization can evaluate domain-specific ownership without relying on string-based type dispatch. The application may retain a generic attachment abstraction at the service layer, but persistence must use explicit FK-backed relations for sensitive ownership paths.
