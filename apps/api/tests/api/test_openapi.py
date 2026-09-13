"""OpenAPI foundation: reflects the real app, no fictitious domain endpoints."""

_FORBIDDEN_DOMAIN_PATH_FRAGMENTS = (
    "person",
    "user",
    "member",
    "guardian",
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
# "group" is deliberately not in the list above: the real Event list
# endpoint (Issue #40) has a documented `group_id` query filter
# (events-api.md §4) referencing the already-implemented Group
# persistence for scope/search purposes — it is not a fictitious Group
# CRUD domain endpoint.


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


def test_openapi_has_no_non_auth_domain_endpoints(real_client) -> None:
    schema = real_client.get("/openapi.json").json()

    # Health (Issue #10), authentication (Issue #33) and the first Event
    # API slice (Issue #40) are the only domain endpoints so far — no
    # Person/Group/Trip/etc. CRUD endpoints have been added under /api/v1.
    assert (
        set(schema["paths"].keys())
        == {"/health/live", "/health/ready"} | _AUTH_PATHS | _EVENT_PATHS
    )
    for fragment in _FORBIDDEN_DOMAIN_PATH_FRAGMENTS:
        assert fragment not in str(schema["paths"]).lower()


def test_swagger_ui_is_served(real_client) -> None:
    response = real_client.get("/docs")

    assert response.status_code == 200
