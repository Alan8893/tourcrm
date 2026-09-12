"""`/api/v1` canonical prefix boundary (ADR-0004, api-conventions.md §2).

The router carries zero routes today (no domain endpoints), so there is
nothing further to assert structurally beyond its prefix; that a request
under the prefix is actually served (through app.main, via our error
contract rather than a raw connection failure) is covered by
test_error_contract.py::test_unknown_endpoint_returns_documented_error_contract.
"""

from app.api.v1.router import router as v1_router


def test_v1_router_uses_canonical_prefix() -> None:
    assert v1_router.prefix == "/api/v1"
