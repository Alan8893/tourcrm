# TourCRM — API Conventions

## 1. Purpose

This document defines mandatory conventions for the TourCRM HTTP API. It is a contract for backend implementation and frontend/integration consumers.

## 2. Base URL and versioning

All application endpoints are under:

`/api/v1/`

Operational endpoints such as health checks may live outside the versioned business API.

Breaking changes require a new major API version. Additive backward-compatible changes remain in the current major version.

## 3. Transport

The API uses HTTP(S) and JSON unless a specific endpoint documents another content type, for example multipart upload for files.

Internet-facing production must use HTTPS. LAN deployment may use HTTP only as an explicitly documented deployment exception.

## 4. Resource naming

Use plural resource nouns in lowercase kebab-free paths:

```text
/users
/persons
/guardian-relationships
/groups
/events
/attendance
/trips
/routes
/achievements
/documents
/equipment
/finance/payments
/notifications
/knowledge/articles
```

Nested resources are allowed when the relationship is meaningful and does not create excessive path depth.

## 5. HTTP methods

Use methods according to semantics:

- GET — read;
- POST — create or execute a non-idempotent command when a resource-oriented operation is not appropriate;
- PUT — complete replacement where supported;
- PATCH — partial update;
- DELETE — logical/resource deletion only where the domain permits deletion.

Business operations that represent explicit state transitions may use command-style subpaths when that is clearer than abusing CRUD.

Example:

`POST /api/v1/events/{event_id}/cancel`

## 6. IDs

Public API resource identifiers must be opaque to clients. The implementation may use integer IDs internally, but the API contract must not require clients to depend on sequential numeric semantics.

A concrete ID format must be selected before implementation and used consistently.

## 7. Timestamps

API timestamps use ISO 8601/RFC 3339 format with timezone information.

Example:

`2026-09-12T14:30:00Z`

The persistence layer should use UTC as the canonical instant representation. User-facing local timezone conversion occurs at the application boundary.

## 8. Create/update response conventions

Successful responses use the canonical API v1 response contract defined by ADR-0014.

For single resources, return the resource representation directly, without a `data` envelope.

For collections, use the canonical envelope:

```json
{
  "items": [],
  "pagination": {
    "page": 1,
    "page_size": 50,
    "total": 0,
    "pages": 0
  }
}
```

Do not introduce `data/meta` as an alternative response envelope in API v1.

## 9. Pagination

Default server-side page size and maximum page size are global configuration values.

Clients must not assume that an endpoint returns the full dataset.

Large exports must use dedicated export endpoints/jobs rather than bypassing pagination.

## 10. Filtering and sorting

Filtering and sorting parameter names must be explicit and documented per endpoint.

Where a generic convention is applicable, prefer:

```text
?search=...
?status=...
?sort=created_at
?order=desc
```

Multi-value filters must use one project-wide convention.

Backend implementations must whitelist sortable/filterable fields rather than interpolating arbitrary client-provided SQL expressions.

## 11. Validation

All external input is untrusted.

Validation occurs at the API boundary and again at the domain/persistence boundary where business invariants require it.

Validation errors are represented using a stable machine-readable error code plus field-level details where applicable.

## 12. Error contract

All expected API errors use the canonical shape established by ADR-0014:

```json
{
  "error": {
    "code": "member_not_found",
    "message": "Member was not found",
    "details": {},
    "request_id": "..."
  }
}
```

Rules:

- `code` is stable and machine-readable;
- `message` is safe for display/logging and must not expose secrets;
- `details` contains structured context, especially validation errors;
- `request_id` identifies the request for correlation with server-side logs.

Internal exception details and stack traces must never be returned in production responses.

## 13. Recommended HTTP status semantics

- `200 OK` — successful read/update/command with response body;
- `201 Created` — resource successfully created;
- `202 Accepted` — asynchronous operation accepted;
- `204 No Content` — successful operation without response body;
- `400 Bad Request` — malformed request not attributable to field validation semantics;
- `401 Unauthorized` — missing/invalid authentication;
- `403 Forbidden` — authenticated but not permitted;
- `404 Not Found` — requested resource unavailable to the caller under the endpoint semantics;
- `409 Conflict` — state/uniqueness/conflict violation;
- `422 Unprocessable Content` — syntactically valid request with validation/business input errors where this distinction is useful;
- `429 Too Many Requests` — rate limit;
- `500 Internal Server Error` — unexpected server error;
- `503 Service Unavailable` — dependency/service unavailable where appropriate.

The project must not use different status meanings for the same category in different modules.

## 14. Authorization

Authentication and authorization are distinct concerns.

Every protected endpoint must evaluate authorization server-side. The effective decision may depend on:

- role;
- permission;
- scope;
- resource ownership;
- group membership;
- guardian relationship;
- event assignment;
- club membership;
- feature settings.

A frontend permission check is only a UX optimization.

## 15. Authentication context

Authenticated requests must have a canonical server-side identity context containing at minimum:

- user identifier;
- person identifier when linked;
- effective roles/permissions as applicable;
- club context;
- session/token metadata required for security.

The exact authentication/session mechanism is defined by the authentication ADR.

## 16. Idempotency

Operations that can be safely retried by clients or gateways must be designed for idempotency.

Create operations that may be retried due to network failure should support an idempotency mechanism when duplicate creation would be harmful, especially for financial operations, external notifications and integrations.

The concrete `Idempotency-Key` policy is to be defined before those modules are implemented.

## 17. Concurrency and optimistic safety

Mutating endpoints must account for concurrent edits where data loss is possible.

For entities such as member profile, event and financial records, the implementation should provide an explicit optimistic-concurrency mechanism where required by the domain.

A concrete mechanism (version column, ETag/If-Match, etc.) is selected per affected domain but must remain consistent within a module.

## 18. Search

Global search, where introduced, must enforce authorization before returning results.

Search results must not reveal the existence of records the caller is not allowed to access.

## 19. File uploads

File uploads use multipart/form-data or a documented upload protocol.

The API must validate:

- authenticated user;
- authorization to attach the file;
- MIME/content type where possible;
- file size;
- allowed extension/type policy;
- storage destination;
- malware scanning strategy if introduced.

The application must not trust client-provided filenames as storage identifiers.

## 20. Async operations

Long-running operations such as large exports, GPX processing or bulk notifications should return `202 Accepted` with an operation/job identifier when asynchronous processing is required.

Clients must have a documented way to retrieve status/result or receive a notification.

## 21. Audit behavior

Protected mutations that are audit-relevant must create audit records as part of the same logical business operation. Audit failure behavior must be documented per category; security-critical mutations must not silently proceed without required auditability.

## 22. Transactions

The API layer must delegate transactional boundaries to the application/domain service layer rather than opening arbitrary independent transactions in route handlers.

Cross-entity operations that form one business action should commit atomically where the database supports it.

## 23. Database leakage prevention

API models must be explicit response schemas. ORM entities must not be returned directly as public API contracts.

The API must not expose:

- password hashes;
- authentication secrets;
- internal storage credentials;
- private infrastructure details;
- internal database exception text.

## 24. OpenAPI

The backend must generate and publish an OpenAPI schema for the implemented versioned API.

The generated schema must be treated as an implementation artifact of the documented contract, not a substitute for domain requirements documentation.

Changes to public API contracts require corresponding documentation updates.

## 25. Deprecation

Deprecated endpoints must be explicitly documented and have:

- deprecation date/version;
- replacement endpoint;
- migration guidance;
- planned removal version where applicable.

Silent breaking changes are prohibited.

## 26. API acceptance checklist

Before an API endpoint is accepted:

- authentication requirement is documented;
- authorization requirement is documented;
- request schema is documented;
- response schema is documented;
- validation and business errors are documented;
- relevant status codes are tested;
- audit requirement is documented;
- idempotency/concurrency requirements are considered;
- OpenAPI reflects the implementation;
- automated tests cover the endpoint and relevant permissions.
