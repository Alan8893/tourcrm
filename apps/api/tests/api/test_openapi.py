"""OpenAPI foundation: reflects the real app, no fictitious domain endpoints."""

_FORBIDDEN_DOMAIN_PATH_FRAGMENTS = (
    "user",
    "trip",
    "route",
    "achievement",
    "document",
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
# API (see _GUARDIAN_RELATIONSHIP_PATHS/_ME_PATHS below).


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
    "/api/v1/events/{event_id}",
    "/api/v1/events/{event_id}/status",
    "/api/v1/events/{event_id}/archive",
}

_PERSON_PATHS = {
    "/api/v1/persons",
    "/api/v1/persons/{person_id}",
    "/api/v1/persons/{person_id}/memberships",
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
}
# No `/api/v1/groups/{group_id}/members/bulk` and no
# `/api/v1/groups/{group_id}/members/{person_id}/transfer` — both are
# deliberately not implemented (people-api.md §15.3-15.4, Issue #71 §3).


def test_openapi_has_no_non_auth_domain_endpoints(real_client) -> None:
    schema = real_client.get("/openapi.json").json()

    # Health (Issue #10), authentication (Issue #33), the first Event API
    # slice (Issue #40), the Person/ClubMembership API (Issue #62), the
    # GuardianRelationship API (Issue #64), and the Group/GroupMembership/
    # GroupInstructorAssignment API (Issue #71) are the only domain
    # endpoints so far — no Trip/etc. CRUD endpoints have been added under
    # /api/v1.
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
    )
    for fragment in _FORBIDDEN_DOMAIN_PATH_FRAGMENTS:
        assert fragment not in str(schema["paths"]).lower()


def test_swagger_ui_is_served(real_client) -> None:
    response = real_client.get("/docs")

    assert response.status_code == 200
