"""OpenAPI foundation: reflects the real app, no fictitious domain endpoints."""

_FORBIDDEN_DOMAIN_PATH_FRAGMENTS = (
    "route",
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
# unimplemented (people-api.md §32). "trip" is no longer forbidden either:
# Issue #245 adds the Trip / TripParticipant tourism-fact foundation (see
# _TRIP_PATHS below) — no route/GPX/tourist-profile endpoints.
# "achievement" is no longer forbidden either: Issue #220 adds the
# Achievement Domain administration API (see _ACHIEVEMENT_PATHS below).


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
    # events-api.md §18 / Issue #175 backend gap: read-only list of the
    # Event's registered participants.
    "/api/v1/events/{event_id}/participants",
    # TH-0117.4 / Issue #162, ADR-0040 §5: read-only participant Document
    # requirement check (valid/missing/expired) for one Event/Person pair.
    "/api/v1/events/{event_id}/document-requirements/{person_id}",
    # Issue #175 backend foundation: every registered participant x every
    # requirement in one response (shared batch readiness evaluator).
    "/api/v1/events/{event_id}/document-requirements/matrix",
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
    # Issue #299 (import-export-ui.md §3.3): one page of the same canonical
    # export dataset for the «Участники мероприятий» report preview.
    "/api/v1/memberships/exports/preview",
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
# Inventory Slice 2 (Issue #230): instances and their movements.

_INVENTORY_QUANTITY_PATHS = {
    "/api/v1/inventory/stock",
    "/api/v1/inventory/items/{item_id}/stock",
    "/api/v1/inventory/items/{item_id}/movements",
    "/api/v1/inventory/items/{item_id}/receipts",
    "/api/v1/inventory/items/{item_id}/transfers",
    "/api/v1/inventory/items/{item_id}/write-offs",
    "/api/v1/inventory/items/{item_id}/write-offs/{movement_id}/reverse",
}
# Inventory Slice 3 (Issue #230): quantity stock and movements — no
# adjustment or stocktake endpoint yet.

_INVENTORY_ISSUE_PATHS = {
    "/api/v1/inventory/issues",
    "/api/v1/inventory/issues/{issue_id}",
    "/api/v1/inventory/issues/{issue_id}/lines",
    "/api/v1/inventory/issues/{issue_id}/lines/{line_id}",
    "/api/v1/inventory/issues/{issue_id}/returns",
    "/api/v1/inventory/issues/{issue_id}/cancel",
    "/api/v1/inventory/issues/{issue_id}/lost",
    "/api/v1/inventory/issues/{issue_id}/movements",
}
# Inventory Slice 4 (Issue #236): issue / return — no DELETE of an issue;
# DELETE of a line only removes it from the issue (the row stays).

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
    # Issue #286: atomic single-participant Transfer (people-api.md §15.3).
    "/api/v1/group-memberships/{membership_id}/transfer",
    "/api/v1/groups/{group_id}/instructors",
    "/api/v1/group-instructor-assignments/{assignment_id}/end",
    # Issue #88 / TH-0083: Group Schedule.
    "/api/v1/groups/{group_id}/schedule",
}
# No `/api/v1/groups/{group_id}/members/bulk` (bulk transfer is out of
# scope, people-api.md §15.4) and no
# `/api/v1/groups/{group_id}/members/{person_id}/transfer` (the only
# Transfer is the per-membership endpoint above, §15.3).

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

# Issue #245: Trip / TripParticipant tourism-fact foundation. No Trip
# status/complete/archive endpoints (a Trip has no lifecycle of its own —
# the Event's lifecycle endpoints apply), no participant add/remove (the
# registration source stays EventParticipation), no correction endpoint.
_TRIP_PATHS = {
    "/api/v1/trips",
    "/api/v1/trips/{event_id}",
    "/api/v1/trips/{event_id}/participants",
    "/api/v1/trips/{event_id}/participants/{person_id}",
}

# Issue #264: the TourismType catalog. No DELETE (deactivate/reactivate
# lifecycle only).
_TOURISM_TYPE_PATHS = {
    "/api/v1/tourism-types",
    "/api/v1/tourism-types/{tourism_type_id}",
    "/api/v1/tourism-types/{tourism_type_id}/activate",
    "/api/v1/tourism-types/{tourism_type_id}/deactivate",
}

# Issue #271: the Country and Region catalogs. No DELETE (deactivate/
# reactivate lifecycle only).
_GEOGRAPHY_PATHS = {
    "/api/v1/countries",
    "/api/v1/countries/{country_id}",
    "/api/v1/countries/{country_id}/activate",
    "/api/v1/countries/{country_id}/deactivate",
    "/api/v1/regions",
    "/api/v1/regions/{region_id}",
    "/api/v1/regions/{region_id}/activate",
    "/api/v1/regions/{region_id}/deactivate",
}

# Issue #220: Achievement Definitions / Rule Versions / Normative Sets /
# Awards / reconciliation. No DELETE anywhere (A1/A2/A4).
_ACHIEVEMENT_PATHS = {
    "/api/v1/achievements/rule-catalog",
    "/api/v1/achievements/definitions",
    "/api/v1/achievements/definitions/{definition_id}",
    "/api/v1/achievements/definitions/{definition_id}/activate",
    "/api/v1/achievements/definitions/{definition_id}/deactivate",
    "/api/v1/achievements/definitions/{definition_id}/rule-versions",
    "/api/v1/achievements/rule-versions/{rule_version_id}",
    "/api/v1/achievements/rule-versions/{rule_version_id}/activate",
    "/api/v1/achievements/rule-versions/{rule_version_id}/deactivate",
    "/api/v1/achievements/normative-sets",
    "/api/v1/achievements/normative-sets/{set_id}",
    "/api/v1/achievements/normative-sets/{set_id}/versions",
    "/api/v1/achievements/normative-versions/{version_id}",
    "/api/v1/achievements/normative-versions/{version_id}/activate",
    "/api/v1/achievements/normative-versions/{version_id}/deactivate",
    "/api/v1/achievements/awards",
    "/api/v1/achievements/awards/{award_id}",
    "/api/v1/achievements/awards/{award_id}/revoke",
    "/api/v1/achievements/reconciliation",
}

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
    # far — plus the later slices listed with their path sets above (the
    # Trip foundation of Issue #245 among them); no route/achievement/etc.
    # endpoints have been added under /api/v1.
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
        | _INVENTORY_QUANTITY_PATHS
        | _INVENTORY_ISSUE_PATHS
        | _TRIP_PATHS
        | _ACHIEVEMENT_PATHS
        | _TOURISM_TYPE_PATHS
        | _GEOGRAPHY_PATHS
    )
    for fragment in _FORBIDDEN_DOMAIN_PATH_FRAGMENTS:
        assert fragment not in str(schema["paths"]).lower()


def test_event_participants_endpoint_is_read_only(real_client) -> None:
    """events-api.md §18: only the read-only GET list is implemented —
    no participant-management POST (not part of this slice)."""
    schema = real_client.get("/openapi.json").json()
    operations = schema["paths"]["/api/v1/events/{event_id}/participants"]
    assert set(operations) == {"get"}
    params = {p["name"] for p in operations["get"]["parameters"] if p["in"] != "cookie"}
    assert params == {"event_id", "page", "page_size"}
    response_schema = operations["get"]["responses"]["200"]["content"]["application/json"]
    item_ref = response_schema["schema"]["$ref"]
    collection = schema["components"]["schemas"][item_ref.rsplit("/", 1)[-1]]
    item_schema_name = collection["properties"]["items"]["items"]["$ref"].rsplit("/", 1)[-1]
    assert set(schema["components"]["schemas"][item_schema_name]["properties"]) == {
        "person_id",
        "first_name",
        "last_name",
        "middle_name",
    }


def test_trip_endpoints_expose_only_their_canonical_methods_and_fields(real_client) -> None:
    """Issue #245: only the canonical Trip / TripParticipant operations and
    fields — no tourism attribute (all deferred), no Trip lifecycle field,
    no registration/participation status on TripParticipant."""
    schema = real_client.get("/openapi.json").json()
    paths = schema["paths"]
    components = schema["components"]["schemas"]

    assert set(paths["/api/v1/trips"]) == {"get", "post"}
    # Issues #264/#268: ordinary Trip editing (TourismType, Official
    # Difficulty) — PATCH only, no separate Difficulty endpoint.
    assert set(paths["/api/v1/trips/{event_id}"]) == {"get", "patch"}
    assert set(paths["/api/v1/trips/{event_id}/participants"]) == {"get"}
    assert set(paths["/api/v1/trips/{event_id}/participants/{person_id}"]) == {"put"}

    list_params = {
        p["name"] for p in paths["/api/v1/trips"]["get"]["parameters"] if p["in"] != "cookie"
    }
    assert list_params == {"page", "page_size", "status"}

    assert set(components["TripCreateRequest"]["properties"]) == {
        "event_id",
        "tourism_type_id",
        "official_difficulty",
        "country_id",
        "region_id",
        "duration_classification",
        "result",
    }
    assert set(components["TripCreateRequest"]["required"]) == {"event_id"}
    assert set(components["TripUpdateRequest"]["properties"]) == {
        "tourism_type_id",
        "official_difficulty",
        "country_id",
        "region_id",
        "duration_classification",
        "result",
    }
    # Issue #268: one structured Official Difficulty — mode, value, source.
    assert set(components["OfficialDifficultyIn"]["properties"]) == {"mode", "value", "source"}
    assert set(components["OfficialDifficultyIn"]["required"]) == {"mode"}
    assert set(components["OfficialDifficultyOut"]["properties"]) == {"mode", "value", "source"}
    assert set(components["TripOut"]["properties"]) == {
        "event_id",
        "tourism_type_id",
        "official_difficulty",
        "country_id",
        "region_id",
        "duration_classification",
        "result",
        "created_at",
        "updated_at",
    }
    # Issue #274: Duration Classification — the three approved values only;
    # never null on the Trip.
    for schema_name in ("TripCreateRequest", "TripUpdateRequest", "TripOut"):
        prop = components[schema_name]["properties"]["duration_classification"]
        assert prop["enum"] == ["ONE_DAY", "MULTI_DAY", "UNCLASSIFIED"], schema_name
    assert "duration_classification" in components["TripOut"]["required"]
    # Issue #276: Result — the three approved values, nullable (no Result).
    for schema_name in ("TripCreateRequest", "TripUpdateRequest", "TripOut"):
        variants = components[schema_name]["properties"]["result"]["anyOf"]
        enums = [variant["enum"] for variant in variants if "enum" in variant]
        assert enums == [["COMPLETED", "PARTIALLY_COMPLETED", "NOT_COMPLETED"]], schema_name
        assert {"type": "null"} in variants, schema_name
    assert set(components["TripParticipantRecordRequest"]["properties"]) == {"actual_participation"}
    assert set(components["TripParticipantOut"]["properties"]) == {
        "event_participation_id",
        "event_id",
        "person_id",
        "actual_participation",
        "created_at",
        "updated_at",
    }


def test_achievement_endpoints_never_expose_delete(real_client) -> None:
    """Issue #220 (A1/A2/A4): Definitions, versions and Awards are never
    physically removed."""
    paths = real_client.get("/openapi.json").json()["paths"]
    for path in _ACHIEVEMENT_PATHS:
        assert "delete" not in paths[path], path
    assert set(paths["/api/v1/achievements/awards/{award_id}"]) == {"get"}
    assert set(paths["/api/v1/achievements/awards/{award_id}/revoke"]) == {"post"}
    # A13: a Rule Version is immutable from creation — read only, no PATCH.
    assert set(paths["/api/v1/achievements/rule-versions/{rule_version_id}"]) == {"get"}
    assert (
        "RuleVersionUpdateRequest"
        not in real_client.get("/openapi.json").json()["components"]["schemas"]
    )
    # A15: the manual Award names its Rule Version explicitly (optional).
    manual = real_client.get("/openapi.json").json()["components"]["schemas"][
        "ManualAwardCreateRequest"
    ]
    assert set(manual["properties"]) == {
        "definition_id",
        "person_id",
        "rule_version_id",
        "verification_note",
    }
    assert set(manual["required"]) == {"definition_id", "person_id"}


def test_tourism_type_endpoints_expose_only_their_canonical_methods(real_client) -> None:
    """Issue #264: catalog CRUD without DELETE plus the two lifecycle
    actions."""
    schema = real_client.get("/openapi.json").json()
    paths = schema["paths"]
    assert set(paths["/api/v1/tourism-types"]) == {"get", "post"}
    assert set(paths["/api/v1/tourism-types/{tourism_type_id}"]) == {"get", "patch"}
    assert set(paths["/api/v1/tourism-types/{tourism_type_id}/activate"]) == {"post"}
    assert set(paths["/api/v1/tourism-types/{tourism_type_id}/deactivate"]) == {"post"}
    assert set(schema["components"]["schemas"]["TourismTypeOut"]["properties"]) == {
        "id",
        "code",
        "name",
        "active",
        "created_at",
        "updated_at",
    }


def test_event_document_matrix_endpoint_shape(real_client) -> None:
    schema = real_client.get("/openapi.json").json()
    operations = schema["paths"]["/api/v1/events/{event_id}/document-requirements/matrix"]
    assert set(operations) == {"get"}
    params = {p["name"] for p in operations["get"]["parameters"] if p["in"] != "cookie"}
    assert params == {"event_id"}
    components = schema["components"]["schemas"]
    response_schema = operations["get"]["responses"]["200"]["content"]["application/json"]
    matrix = components[response_schema["schema"]["$ref"].rsplit("/", 1)[-1]]
    assert set(matrix["properties"]) == {"event_id", "participants"}
    row = components[matrix["properties"]["participants"]["items"]["$ref"].rsplit("/", 1)[-1]]
    assert set(row["properties"]) == {
        "person_id",
        "first_name",
        "last_name",
        "middle_name",
        "requirements",
    }
    cell = components[row["properties"]["requirements"]["items"]["$ref"].rsplit("/", 1)[-1]]
    assert set(cell["properties"]) == {"document_type", "required", "result"}
    assert cell["properties"]["result"]["enum"] == ["valid", "missing", "expired"]


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


def test_inventory_quantity_endpoints_expose_only_their_canonical_methods(real_client) -> None:
    """Inventory Slice 3: stock is read-only (no PUT/PATCH/DELETE); it changes
    only through the movement endpoints; the Slice 2 reversal is untouched."""
    schema = real_client.get("/openapi.json").json()
    paths = schema["paths"]
    item = "/api/v1/inventory/items/{item_id}"

    for path in ("/api/v1/inventory/stock", f"{item}/stock", f"{item}/movements"):
        assert set(paths[path]) == {"get"}
    for action in ("receipts", "transfers", "write-offs", "write-offs/{movement_id}/reverse"):
        assert set(paths[f"{item}/{action}"]) == {"post"}
    assert (
        paths["/api/v1/inventory/movements/{movement_id}/reverse"]["post"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/InventoryInstanceOut"
    )

    schemas = schema["components"]["schemas"]
    assert set(schemas["InventoryReceiptRequest"]["properties"]) == {
        "storage_location_id",
        "quantity",
        "unit_cost_minor",
        "comment",
    }
    assert set(schemas["InventoryTransferRequest"]["properties"]) == {
        "from_location_id",
        "to_location_id",
        "quantity",
        "comment",
    }
    assert set(schemas["InventoryWriteOffRequest"]["required"]) == {
        "storage_location_id",
        "quantity",
        "comment",
    }
    assert set(schemas["InventoryStockOut"]["properties"]) == {
        "item_id",
        "storage_location_id",
        "quantity",
    }


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
        assert (
            "requestBody"
            not in paths[f"/api/v1/memberships/imports/{{import_id}}/{action}"]["post"]
        )
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


def test_membership_export_preview_endpoint_and_schemas_are_exact(real_client) -> None:
    """Issue #299: the report preview is one POST on the export resource
    taking the export's own dataset selection (no `format`, no dataset
    source such as `person_ids`) plus page bounds, and returning one page
    of the same columns/rows with the standard pagination envelope."""
    schema = real_client.get("/openapi.json").json()
    paths = schema["paths"]
    components = schema["components"]["schemas"]

    assert set(paths["/api/v1/memberships/exports/preview"]) == {"post"}
    request_schema = components["ParticipantExportPreviewRequest"]
    assert set(request_schema["properties"]) == {
        "context",
        "group_id",
        "event_id",
        "membership_status",
        "participation_status",
        "fields",
        "page",
        "page_size",
    }
    assert request_schema["additionalProperties"] is False
    assert set(request_schema["required"]) == {"context", "fields"}
    assert request_schema["properties"]["page_size"]["maximum"] == 100

    response_schema = components["ParticipantExportPreviewOut"]
    assert set(response_schema["properties"]) == {"title", "columns", "items", "pagination"}
    assert set(components["ExportColumnOut"]["properties"]) == {"field_code", "label"}


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


def test_geography_endpoints_expose_only_their_canonical_methods(real_client) -> None:
    """Issue #271: Country/Region catalog CRUD without DELETE plus the two
    lifecycle actions; provenance (`source_type`/`source_reference`) on
    both, Region bound to one Country."""
    schema = real_client.get("/openapi.json").json()
    paths = schema["paths"]
    components = schema["components"]["schemas"]
    for collection, item in (("countries", "country_id"), ("regions", "region_id")):
        assert set(paths[f"/api/v1/{collection}"]) == {"get", "post"}
        assert set(paths[f"/api/v1/{collection}/{{{item}}}"]) == {"get", "patch"}
        assert set(paths[f"/api/v1/{collection}/{{{item}}}/activate"]) == {"post"}
        assert set(paths[f"/api/v1/{collection}/{{{item}}}/deactivate"]) == {"post"}
    common = {
        "id",
        "code",
        "name",
        "active",
        "source_type",
        "source_reference",
        "created_at",
        "updated_at",
    }
    assert set(components["CountryOut"]["properties"]) == common
    assert set(components["RegionOut"]["properties"]) == common | {"country_id", "semantic_type"}
    assert set(components["CountryCreateRequest"]["required"]) == {"code", "name"}
    assert set(components["RegionCreateRequest"]["required"]) == {
        "country_id",
        "code",
        "name",
        "semantic_type",
    }
    # §9: semantic type is fixed at creation — not part of ordinary editing.
    assert "semantic_type" not in components["RegionUpdateRequest"]["properties"]
