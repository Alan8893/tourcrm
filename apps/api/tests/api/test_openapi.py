"""OpenAPI foundation: reflects the real app, no fictitious domain endpoints."""

_FORBIDDEN_DOMAIN_PATH_FRAGMENTS = (
    "person",
    "user",
    "member",
    "guardian",
    "group",
    "event",
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


def test_openapi_schema_is_served(real_client) -> None:
    response = real_client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "TourCRM API"


def test_openapi_has_no_domain_endpoints(real_client) -> None:
    schema = real_client.get("/openapi.json").json()

    assert schema["paths"] == {}
    for fragment in _FORBIDDEN_DOMAIN_PATH_FRAGMENTS:
        assert fragment not in str(schema["paths"]).lower()


def test_swagger_ui_is_served(real_client) -> None:
    response = real_client.get("/docs")

    assert response.status_code == 200
