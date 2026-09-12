# ADR-0014: Canonical API response envelope

## Status
Accepted

## Context

The API documentation contained two collection conventions: `items/pagination` and `data/meta`. A single contract is required for generated clients, frontend code and tests.

## Decision

TourCRM API v1 uses the following canonical response shapes.

### Single resource

The resource is returned directly as the response body.

### Collection

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

### Error

```json
{
  "error": {
    "code": "resource_not_found",
    "message": "Resource was not found",
    "details": {},
    "request_id": "..."
  }
}
```

## Rules

1. Do not introduce `data/meta` as an alternative collection envelope in API v1.
2. Endpoint-specific metadata may be added only in a documented extension of the canonical envelope.
3. OpenAPI schemas must reflect the canonical contract.
4. Existing text that permits arbitrary choice is superseded by this ADR.

## Consequences

Frontend and integration clients can rely on one collection contract. Existing references to `data/meta` in general API conventions are legacy wording and must be synchronized before dependent endpoint implementation.
