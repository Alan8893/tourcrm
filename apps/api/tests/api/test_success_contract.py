"""Success response shapes per ADR-0014: single resource returned directly,
collections as items+pagination. No `data`/`meta` envelope anywhere.
"""


def test_single_resource_response_is_returned_directly(probe_client) -> None:
    response = probe_client.get("/probe/abc-123")

    assert response.status_code == 200
    body = response.json()
    assert body == {"id": "abc-123", "name": "example"}
    assert "data" not in body
    assert "meta" not in body


def test_collection_response_has_items_and_pagination(probe_client) -> None:
    response = probe_client.get("/probe-collection")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "pagination"}
    assert isinstance(body["items"], list)
    assert body["pagination"] == {"page": 1, "page_size": 50, "total": 2, "pages": 1}
    assert "data" not in body
    assert "meta" not in body


def test_successful_foundation_request_carries_request_id_header(probe_client) -> None:
    response = probe_client.get("/probe/abc-123")

    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")
