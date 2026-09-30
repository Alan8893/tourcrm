"""OpenAPI foundation: reflects the real app, no fictitious domain endpoints."""

_FORBIDDEN_DOMAIN_PATH_FRAGMENTS = (
    "trip",
    "route",
    "achievement",
    "equipment",
    "payment",
    "finance",
    "notification",
    "knowledge",
)
# "group" is deliberately not in the list above: Issue #71 adds the real
# Group/GroupMembership/GroupInstructorAssignment API (see _GROUP_PATHS
# below), and the Event list endpoint (Issue #40) already had a documented
# `group_id` query filter (events-api.md §4) before that. "person"/"member"
# are likewise no longer forbidden: Issue #62 adds the real Person/
# ClubMembership API (see _PERSON_PATHS/_MEMBERSHIP_PATHS below). "guardian"
# is no longer forbidden either: Issue #64 adds the real GuardianRelationship
# API (see _GUARDIAN_RELATIONSHIP_PATHS/_ME_PATHS below). "user" is no
# longer forbidden either: this check scans the full serialized `paths`
# object, not just route strings, so it also matches query-parameter and
# schema-property names — Issue #74's `GET /role-assignments?user_id=...`
# filter (an inline query parameter, always literally embedded under
# `paths`, unlike a request-body field which is `$ref`'d to
# `components/schemas` and so was never caught by this check even before
# this change) is exactly such a case. `User` itself is not a fictitious
# domain this guard was ever meant to catch — it has existed since
# Issue #19's identity foundation. TH-0107 adds one read-only directory
# endpoint, `GET /users` (see _USER_PATHS below) — not the full admin User
# Management API endpoint-inventory.md §2 documents; the rest of that
# section (`GET/PATCH /users/{id}`, `POST /users`, block/disable/activate/
# archive, sessions) remains unimplemented. "document" is no longer
# forbidden either: TH-0117.3 / Issue #160 adds the real participant
# Document API foundation (see _DOCUMENT_PATHS below) — create/list/
# detail/download only; replace/revoke/EventDocumentRequirement remain
# unimplemented (people-api.md §32).


def test_openapi_schema_is_served(real_client) -> None:
    response = real_client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "TourCRM API"


_AUTH_PATHS = {
    "/api/v1/auth/register",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/resend-verification",
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/auth/me",
    "/api/v1/auth/sessions",
    "/api/v1/auth/sessions/{session_id}",
    "/api/v1/auth/logout-all",
    "/api/v1/auth/password-reset/request",
    "/api/v1/auth/password-reset/confirm",
    "/api/v1/auth/password/change",
}

_EVENT_PATHS = {
    "/api/v1/events",
    "/api/v1/events/calendar",
    # Issue #91 / TH-0085: conflict detection.
    "/api/v1/events/conflicts",
    "/api/v1/events/{event_id}",
    "/api/v1/events/{event_id}/status",
    "/api/v1/events/{event_id}/archive",
    # Issue #94 / TH-0087: Attendance.
    "/api/v1/events/{event_id}/attendance",
    "/api/v1/events/{event_id}/attendance/{person_id}",
    "/api/v1/events/{event_id}/attendance/{person_id}/corrections",
    # TH-0108.2 / ADR-0037: participant self-registration/withdrawal.
    "/api/v1/events/{event_id}/participation",
    # TH-0117.4 / Issue #162, ADR-0040 §5: read-only participant Document
    # requirement check (valid/missing/expired) for one Event/Person pair.
    "/api/v1/events/{event_id}/document-requirements/{person_id}",
    # TH-0117.5 / Issue #164, ADR-0040 §5, events-api.md §31.1: manage the
    # EventDocumentRequirement records themselves (list/create/update/delete).
    "/api/v1/events/{event_id}/document-requirements",
    "/api/v1/events/{event_id}/document-requirements/{requirement_id}",
    # TH-0117.9 / Issue #172, ADR-0040 §5/§6/§7, events-api.md §31.5: the
    # synchronous competition document package export.
    "/api/v1/events/{event_id}/document-package",
}

_PERSON_PATHS = {
    "/api/v1/persons",
    # TH-0116 / GitHub Issue #150: the atomic Person-creation wizard —
    # Person + account provisioning + initial RoleAssignment + role-
    # specific contextual setup, all as one operation. Additive: does not
    # replace or change the bare `POST /persons` contract above.
    "/api/v1/persons/wizard",
    "/api/v1/persons/{person_id}",
    "/api/v1/persons/{person_id}/memberships",
    # TH-0116 / GitHub Issue #150: the reverse direction of `GET /groups/
    # {group_id}/members` — closes a pre-existing documentation-only gap
    # (people-api.md §17 already described this endpoint).
    "/api/v1/persons/{person_id}/groups",
    # TH-0112 / ADR-0039: Person-scoped system role management, a
    # canonical-role-code-only view onto the same flat `/role-assignments`
    # resource (see _ROLE_ASSIGNMENT_PATHS below and ADR-0025 §6).
    "/api/v1/persons/{person_id}/role-assignments",
    "/api/v1/persons/{person_id}/role-assignments/{role_code}",
    # TH-0113 / ADR-0038: administrative User account creation and
    # password reset/first-access setup for a Person.
    "/api/v1/persons/{person_id}/account",
    "/api/v1/persons/{person_id}/account/password-reset",
}

_DOCUMENT_PATHS = {
    # TH-0117.3 / Issue #160, ADR-0040: participant Document API
    # foundation. Nested-only — no flat `/documents/{id}` resource in this
    # slice. No EventDocumentRequirement endpoints here
    # (people-api.md §32, Issue #160 §11).
    "/api/v1/persons/{person_id}/documents",
    # Also carries PATCH (TH-0117.8 / Issue #170, ADR-0040 §4/§7):
    # non-file metadata correction of the current version, in place —
    # the path string itself is unchanged, only its method set grows.
    "/api/v1/persons/{person_id}/documents/{document_id}",
    "/api/v1/persons/{person_id}/documents/{document_id}/download",
    # TH-0117.6 / Issue #166, ADR-0040 §4: participant Document
    # replacement / new version.
    "/api/v1/persons/{person_id}/documents/{document_id}/replace",
    # TH-0117.7 / Issue #168, ADR-0040 §4: participant Document
    # revocation.
    "/api/v1/persons/{person_id}/documents/{document_id}/revoke",
}

_PERSON_PHOTO_PATHS = {
    # TH-0119 / Issue #176 (profile-photo-api.md): GET/PUT/DELETE of the
    # current profile photo — no other avatar endpoints.
    "/api/v1/persons/{person_id}/photo",
}

_MEMBERSHIP_PATHS = {
    "/api/v1/memberships",
    "/api/v1/memberships/{membership_id}",
    "/api/v1/memberships/{membership_id}/status",
}

_MEMBERSHIP_IMPORT_PATHS = {
    # TH-0118.1 / Issue #185 (people-api.md §22, endpoint-inventory.md
    # §4.1): participant import job foundation — create/status/errors
    # only; no parse/preview/approve/apply endpoints in this slice.
    "/api/v1/memberships/imports",
    "/api/v1/memberships/imports/{import_id}",
    "/api/v1/memberships/imports/{import_id}/errors",
    # TH-0118.2: explicit, synchronous parse/validate/duplicate-check
    # dry-run (people-api.md §22 "POST .../preview").
    "/api/v1/memberships/imports/{import_id}/preview",
    # TH-0118.3: explicit approval and synchronous apply (people-api.md
    # §22 "POST .../approve", "POST .../apply"); no separate report path.
    "/api/v1/memberships/imports/{import_id}/approve",
    "/api/v1/memberships/imports/{import_id}/apply",
}

_MEMBERSHIP_EXPORT_PATHS = {
    # TH-0118.4 / Issue #218 (participant-export-api.md): synchronous,
    # Administrator-only participant export (xlsx/pdf/print) and the
    # canonical export-field allowlist; no saved templates (#217).
    "/api/v1/memberships/exports",
    "/api/v1/memberships/exports/fields",
    # PR #226 PO decision: backend-authoritative filter vocabularies
    # (participation_status values + labels) for the export wizard.
    "/api/v1/memberships/exports/filters",
}

# TH-0120 / Issue #227 (docs/04-ux/news.md): News / Announcements. No
# `DELETE /api/v1/news/{news_id}` — deletion is the archive action and the
# News record is never physically removed.
_NEWS_PATHS = {
    "/api/v1/news",
    "/api/v1/news/{news_id}",
    "/api/v1/news/{news_id}/publish",
    "/api/v1/news/{news_id}/archive",
    "/api/v1/news/{news_id}/image",
}

_INVENTORY_PATHS = {
    f"/api/v1/inventory/{collection}{suffix}"
    for collection, record in (
        ("categories", "category_id"),
        ("units", "unit_id"),
        ("storage-locations", "location_id"),
        ("items", "item_id"),
    )
    for suffix in ("", f"/{{{record}}}", f"/{{{record}}}/archive")
}
# TH-0121 / Issue #230: Inventory Foundation reference data and
# nomenclature.

_INVENTORY_INSTANCE_PATHS = {
    "/api/v1/inventory/instances",
    "/api/v1/inventory/instances/{instance_id}",
    "/api/v1/inventory/instances/{instance_id}/transfer",
    "/api/v1/inventory/instances/{instance_id}/repair-start",
    "/api/v1/inventory/instances/{instance_id}/repair-end",
    "/api/v1/inventory/instances/{instance_id}/write-off",
    "/api/v1/inventory/instances/{instance_id}/movements",
    "/api/v1/inventory/movements/{movement_id}/reverse",
}
# Inventory Slice 2 (Issue #230): instances and their movements only — no
# issue/return/stocktake/quantity-movement endpoint yet.

_GUARDIAN_RELATIONSHIP_PATHS = {
    "/api/v1/persons/{person_id}/guardian-relationships",
    "/api/v1/guardian-relationships/{relationship_id}",
    "/api/v1/guardian-relationships/{relationship_id}/terminate",
}

_ME_PATHS = {
    "/api/v1/me/children",
    # Issue #88 / TH-0083: Instructor Schedule.
    "/api/v1/me/instructor-schedule",
}

_GROUP_PATHS = {
    "/api/v1/groups",
    "/api/v1/groups/{group_id}",
    "/api/v1/groups/{group_id}/archive",
    "/api/v1/groups/{group_id}/members",
    "/api/v1/group-memberships/{membership_id}",
    "/api/v1/group-memberships/{membership_id}/end",
    "/api/v1/groups/{group_id}/instructors",
    "/api/v1/group-instructor-assignments/{assignment_id}/end",
    # Issue #88 / TH-0083: Group Schedule.
    "/api/v1/groups/{group_id}/schedule",
}
# No `/api/v1/groups/{group_id}/members/bulk` and no
# `/api/v1/groups/{group_id}/members/{person_id}/transfer` — both are
# deliberately not implemented (people-api.md §15.3-15.4, Issue #71 §3).

_USER_PATHS = {
    "/api/v1/users",
}

_ROLE_ASSIGNMENT_PATHS = {
    "/api/v1/role-assignments",
    "/api/v1/role-assignments/{assignment_id}/revoke",
}
# No `/api/v1/roles`, `/api/v1/permissions`, `/api/v1/audit-logs*` — all
# deliberately out of Issue #74's scope (endpoint-inventory.md §24,
# ADR-0026's explicit non-goals).

_EVENT_RECURRENCE_PATHS = {
    "/api/v1/events/series",
    "/api/v1/events/series/{series_id}",
    "/api/v1/events/series/{series_id}/pause",
    "/api/v1/events/series/{series_id}/resume",
    "/api/v1/events/series/{series_id}/cancel",
    "/api/v1/events/series/{series_id}/archive",
    "/api/v1/events/series/{series_id}/exceptions",
    "/api/v1/events/series/{series_id}/occurrences",
    "/api/v1/events/occurrences/{occurrence_id}",
}
# No dedicated `/api/v1/events/occurrences/{occurrence_id}/cancel` or
# `/reschedule` — both deliberately not canonical (docs/05-api/
# event-recurrence-api.md, Issue #79): occurrence-level reschedule/
# cancellation/allow-listed overrides go exclusively through
# `POST /events/series/{series_id}/exceptions`.


def test_openapi_has_no_non_auth_domain_endpoints(real_client) -> None:
    schema = real_client.get("/openapi.json").json()

    # Health (Issue #10), authentication (Issue #33), the first Event API
    # slice (Issue #40), the Person/ClubMembership API (Issue #62), the
    # GuardianRelationship API (Issue #64), the Group/GroupMembership/
    # GroupInstructorAssignment API (Issue #71), the RoleAssignment API
    # (Issue #74), the Event recurrence API (Issue #79), the read-only
    # User directory (TH-0107), and the participant Document API
    # foundation (TH-0117.3 / Issue #160) are the only domain endpoints so
    # far — no Trip/etc. CRUD endpoints have been added under /api/v1.
    assert (
        set(schema["paths"].keys())
        == {"/health/live", "/health/ready"}
        | _AUTH_PATHS
        | _EVENT_PATHS
        | _PERSON_PATHS
        | _PERSON_PHOTO_PATHS
        | _MEMBERSHIP_PATHS
        | _MEMBERSHIP_IMPORT_PATHS
        | _MEMBERSHIP_EXPORT_PATHS
        | _GUARDIAN_RELATIONSHIP_PATHS
        | _ME_PATHS
        | _GROUP_PATHS
        | _USER_PATHS
        | _ROLE_ASSIGNMENT_PATHS
        | _EVENT_RECURRENCE_PATHS
        | _DOCUMENT_PATHS
        | _NEWS_PATHS
        | _INVENTORY_PATHS
        | _INVENTORY_INSTANCE_PATHS
    )
    for fragment in _FORBIDDEN_DOMAIN_PATH_FRAGMENTS:
        assert fragment not in str(schema["paths"]).lower()


def test_news_endpoints_expose_only_their_canonical_methods(real_client) -> None:
    """TH-0120 / Issue #227: no physical DELETE of a News; lifecycle
    changes only through the publish/archive actions (no request body);
    the image is a multipart sub-resource like the profile photo."""
    schema = real_client.get("/openapi.json").json()
    paths = schema["paths"]

    assert set(paths["/api/v1/news"]) == {"get", "post"}
    assert set(paths["/api/v1/news/{news_id}"]) == {"get", "patch"}
    assert set(paths["/api/v1/news/{news_id}/publish"]) == {"post"}
    assert set(paths["/api/v1/news/{news_id}/archive"]) == {"post"}
    assert set(paths["/api/v1/news/{news_id}/image"]) == {"get", "put", "delete"}
    for action in ("publish", "archive"):
        assert "requestBody" not in paths[f"/api/v1/news/{{news_id}}/{action}"]["post"]
    image_body = paths["/api/v1/news/{news_id}/image"]["put"]["requestBody"]
    assert set(image_body["content"]) == {"multipart/form-data"}
    update_schema = schema["components"]["schemas"]["NewsUpdateRequest"]
    assert "status" not in update_schema["properties"]


def test_inventory_endpoints_expose_only_their_canonical_methods(real_client) -> None:
    """TH-0121 / Issue #230: no physical DELETE (archive is the only
    removal, inventory.md §17); archive actions take no body; no request
    schema can set a nomenclature quantity/stock/status."""
    schema = real_client.get("/openapi.json").json()
    paths = schema["paths"]

    for collection, record in (
        ("categories", "category_id"),
        ("units", "unit_id"),
        ("storage-locations", "location_id"),
        ("items", "item_id"),
    ):
        base = f"/api/v1/inventory/{collection}"
        assert set(paths[base]) == {"get", "post"}
        assert set(paths[f"{base}/{{{record}}}"]) == {"get", "patch"}
        assert set(paths[f"{base}/{{{record}}}/archive"]) == {"post"}
        assert "requestBody" not in paths[f"{base}/{{{record}}}/archive"]["post"]

    schemas = schema["components"]["schemas"]
    for name in ("InventoryItemCreateRequest", "InventoryItemUpdateRequest"):
        assert set(schemas[name]["properties"]) == {
            "name",
            "category_id",
            "unit_id",
            "accounting_mode",
            "current_cost_minor",
        }


def test_inventory_instance_endpoints_expose_only_their_canonical_methods(real_client) -> None:
    """Inventory Slice 2: no DELETE; state/location/item/inventory number
    are not writable through PATCH; repair actions take no body."""
    schema = real_client.get("/openapi.json").json()
    paths = schema["paths"]
    base = "/api/v1/inventory/instances"

    assert set(paths[base]) == {"get", "post"}
    assert set(paths[f"{base}/{{instance_id}}"]) == {"get", "patch"}
    assert set(paths[f"{base}/{{instance_id}}/movements"]) == {"get"}
    for action in ("transfer", "repair-start", "repair-end", "write-off"):
        assert set(paths[f"{base}/{{instance_id}}/{action}"]) == {"post"}
    for action in ("repair-start", "repair-end"):
        assert "requestBody" not in paths[f"{base}/{{instance_id}}/{action}"]["post"]
    assert set(paths["/api/v1/inventory/movements/{movement_id}/reverse"]) == {"post"}

    schemas = schema["components"]["schemas"]
    assert set(schemas["InventoryInstanceUpdateRequest"]["properties"]) == {
        "manufacturer_barcode",
        "manufacturer_serial_number",
        "description",
    }
    assert set(schemas["InventoryInstanceCreateRequest"]["properties"]) == {
        "item_id",
        "storage_location_id",
        "unit_cost_minor",
        "manufacturer_barcode",
        "manufacturer_serial_number",
        "description",
    }
    assert schemas["InventoryInstanceWriteOffRequest"]["required"] == ["comment"]
    list_parameters = {p["name"] for p in paths[base]["get"]["parameters"]}
    assert {"item_id", "state", "storage_location_id"} <= list_parameters


def test_membership_import_endpoints_expose_only_their_canonical_methods(real_client) -> None:
    """TH-0118.1 / Issue #185: POST (multipart) creates a job; the job and
    its errors are read-only. TH-0118.2 adds only POST .../preview and
    TH-0118.3 only POST .../approve and POST .../apply — no endpoint sets a
    job's status directly.
    """
    paths = real_client.get("/openapi.json").json()["paths"]

    assert set(paths["/api/v1/memberships/imports"]) == {"post"}
    assert set(paths["/api/v1/memberships/imports/{import_id}"]) == {"get"}
    assert set(paths["/api/v1/memberships/imports/{import_id}/errors"]) == {"get"}
    assert set(paths["/api/v1/memberships/imports/{import_id}/preview"]) == {"post"}
    assert set(paths["/api/v1/memberships/imports/{import_id}/approve"]) == {"post"}
    assert set(paths["/api/v1/memberships/imports/{import_id}/apply"]) == {"post"}
    for action in ("approve", "apply"):
        assert "requestBody" not in paths[f"/api/v1/memberships/imports/{{import_id}}/{action}"][
            "post"
        ]
    severity = next(
        parameter
        for parameter in paths["/api/v1/memberships/imports/{import_id}/errors"]["get"][
            "parameters"
        ]
        if parameter["name"] == "severity"
    )
    assert "error" in str(severity["schema"]) and "warning" in str(severity["schema"])
    request_body = paths["/api/v1/memberships/imports"]["post"]["requestBody"]
    assert set(request_body["content"]) == {"multipart/form-data"}
    assert "201" in paths["/api/v1/memberships/imports"]["post"]["responses"]


def test_membership_export_endpoints_and_request_schema_are_exact(real_client) -> None:
    """TH-0118.4 / Issue #218: one POST export endpoint for every format
    (xlsx/pdf/print) and one GET allowlist endpoint — no per-format paths,
    and the request schema admits no dataset source such as `person_ids`."""
    schema = real_client.get("/openapi.json").json()
    paths = schema["paths"]

    assert set(paths["/api/v1/memberships/exports"]) == {"post"}
    assert set(paths["/api/v1/memberships/exports/fields"]) == {"get"}
    assert set(paths["/api/v1/memberships/exports/filters"]) == {"get"}
    filters_schema = schema["components"]["schemas"]["ExportFiltersOut"]
    assert set(filters_schema["properties"]) == {"participation_status"}
    option_schema = schema["components"]["schemas"]["ExportFilterOptionOut"]
    assert set(option_schema["properties"]) == {"value", "label"}
    responses = paths["/api/v1/memberships/exports"]["post"]["responses"]
    assert set(responses["200"]["content"]) == {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/pdf",
        "text/html",
    }

    request_schema = schema["components"]["schemas"]["ParticipantExportRequest"]
    assert set(request_schema["properties"]) == {
        "context",
        "group_id",
        "event_id",
        "membership_status",
        "participation_status",
        "fields",
        "format",
    }
    assert request_schema["additionalProperties"] is False
    assert set(request_schema["required"]) == {"context", "fields", "format"}
    assert request_schema["properties"]["context"]["enum"] == [
        "club",
        "group",
        "event",
        "group_event",
    ]
    assert request_schema["properties"]["format"]["enum"] == ["xlsx", "pdf", "print"]


def test_document_metadata_patch_endpoint_and_schemas_are_exact(real_client) -> None:
    """TH-0117.8 / Issue #170: the PATCH method on the existing
    `/persons/{person_id}/documents/{document_id}` path, its request
    body schema (`DocumentMetadataUpdateRequest` — only `issued_at`/
    `expires_at`), and its 200 response schema (the existing
    `DocumentOut`, shared with the GET on the same path)."""
    schema = real_client.get("/openapi.json").json()
    path_item = schema["paths"]["/api/v1/persons/{person_id}/documents/{document_id}"]

    assert "patch" in path_item
    patch_operation = path_item["patch"]

    request_schema_ref = patch_operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    request_schema_name = request_schema_ref.rsplit("/", 1)[-1]
    request_schema = schema["components"]["schemas"][request_schema_name]
    assert set(request_schema.get("properties", {})) == {"issued_at", "expires_at"}

    response_schema_ref = patch_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    assert response_schema_ref.rsplit("/", 1)[-1] == "DocumentOut"

    get_response_schema_ref = path_item["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    assert get_response_schema_ref == response_schema_ref


def test_swagger_ui_is_served(real_client) -> None:
    response = real_client.get("/docs")

    assert response.status_code == 200
