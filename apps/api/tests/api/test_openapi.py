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
    "/api/v1/persons/{person_id}/documents/{document_id}",
    "/api/v1/persons/{person_id}/documents/{document_id}/download",
    # TH-0117.6 / Issue #166, ADR-0040 §4: participant Document
    # replacement / new version.
    "/api/v1/persons/{person_id}/documents/{document_id}/replace",
    # TH-0117.7 / Issue #168, ADR-0040 §4: participant Document
    # revocation.
    "/api/v1/persons/{person_id}/documents/{document_id}/revoke",
}

_MEMBERSHIP_PATHS = {
    "/api/v1/memberships",
    "/api/v1/memberships/{membership_id}",
    "/api/v1/memberships/{membership_id}/status",
}

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
        | _MEMBERSHIP_PATHS
        | _GUARDIAN_RELATIONSHIP_PATHS
        | _ME_PATHS
        | _GROUP_PATHS
        | _USER_PATHS
        | _ROLE_ASSIGNMENT_PATHS
        | _EVENT_RECURRENCE_PATHS
        | _DOCUMENT_PATHS
    )
    for fragment in _FORBIDDEN_DOMAIN_PATH_FRAGMENTS:
        assert fragment not in str(schema["paths"]).lower()


def test_swagger_ui_is_served(real_client) -> None:
    response = real_client.get("/docs")

    assert response.status_code == 200
