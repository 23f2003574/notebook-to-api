import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.request_router import (
    RequestRouter,
    RouteMatch,
    RoutingResult,
    get_request_router,
    router as request_router_router,
)
from backend.gateway.route_registry import (
    InvalidMethodError,
    RouteNotFoundError,
    RouteRegistry,
)


@pytest.fixture
def registry() -> RouteRegistry:
    reg = RouteRegistry()
    reg.register("/notebooks", ["GET", "POST"])
    reg.register("/notebooks/{id}", ["GET", "DELETE"])
    return reg


@pytest.fixture
def request_router(registry: RouteRegistry) -> RequestRouter:
    return RequestRouter(registry)


@pytest.fixture
def client(request_router: RequestRouter) -> TestClient:
    app = FastAPI()
    app.include_router(request_router_router)
    app.dependency_overrides[get_request_router] = lambda: request_router
    return TestClient(app)


def test_match_returns_route_for_exact_path(request_router: RequestRouter):
    match = request_router.match("/notebooks", "GET")

    assert isinstance(match, RouteMatch)
    assert match.template == "/notebooks"
    assert match.params == {}


def test_match_extracts_path_parameters(request_router: RequestRouter):
    match = request_router.match("/notebooks/abc123", "GET")

    assert match.template == "/notebooks/{id}"
    assert match.params == {"id": "abc123"}


def test_match_unknown_path_raises(request_router: RequestRouter):
    with pytest.raises(RouteNotFoundError):
        request_router.match("/does-not-exist")


def test_match_wrong_method_raises(request_router: RequestRouter):
    with pytest.raises(InvalidMethodError):
        request_router.match("/notebooks/abc123", "POST")


def test_resolve_returns_matched_result(request_router: RequestRouter):
    result = request_router.resolve("/notebooks/abc123", "GET")

    assert isinstance(result, RoutingResult)
    assert result.matched is True
    assert result.params == {"id": "abc123"}
    assert result.reason == "matched"


def test_resolve_returns_not_found_without_raising(request_router: RequestRouter):
    result = request_router.resolve("/does-not-exist")

    assert result.matched is False
    assert result.reason == "not_found"
    assert result.route is None


def test_resolve_returns_method_not_allowed_without_raising(request_router: RequestRouter):
    result = request_router.resolve("/notebooks/abc123", "POST")

    assert result.matched is False
    assert result.reason == "method_not_allowed"


def test_fallback_returns_unmatched_result_by_default(request_router: RequestRouter):
    result = request_router.fallback("/does-not-exist")

    assert result.matched is False
    assert result.reason == "not_found"


def test_fallback_uses_custom_handler():
    registry = RouteRegistry()
    calls = []

    def custom_fallback(path, method):
        calls.append((path, method))
        return RoutingResult(matched=False, path=path, method=method, route=None, reason="custom")

    router = RequestRouter(registry, fallback_handler=custom_fallback)

    result = router.route("/does-not-exist", "GET")

    assert result.reason == "custom"
    assert calls == [("/does-not-exist", "GET")]


def test_route_returns_matched_result_when_found(request_router: RequestRouter):
    result = request_router.route("/notebooks", "GET")

    assert result.matched is True
    assert result.route.path == "/notebooks"


def test_route_falls_back_when_unmatched(request_router: RequestRouter):
    result = request_router.route("/does-not-exist")

    assert result.matched is False
    assert result.reason == "not_found"


def test_route_falls_back_when_method_not_allowed(request_router: RequestRouter):
    result = request_router.route("/notebooks/abc123", "POST")

    assert result.matched is False
    assert result.reason == "method_not_allowed"


def test_api_route_returns_matched_result(client: TestClient):
    response = client.post("/gateway/route", json={"path": "/notebooks/abc123", "method": "GET"})

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["params"] == {"id": "abc123"}


def test_api_route_returns_fallback_result_when_unmatched(client: TestClient):
    response = client.post("/gateway/route", json={"path": "/does-not-exist", "method": "GET"})

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is False
    assert body["reason"] == "not_found"


def test_api_match_returns_params(client: TestClient):
    response = client.get("/gateway/routes/match", params={"path": "/notebooks/abc123", "method": "GET"})

    assert response.status_code == 200
    assert response.json()["params"] == {"id": "abc123"}


def test_api_match_unknown_path_returns_404(client: TestClient):
    response = client.get("/gateway/routes/match", params={"path": "/does-not-exist"})

    assert response.status_code == 404


def test_api_match_wrong_method_returns_405(client: TestClient):
    response = client.get(
        "/gateway/routes/match", params={"path": "/notebooks/abc123", "method": "POST"}
    )

    assert response.status_code == 405


def test_api_resolve_returns_200_even_when_unmatched(client: TestClient):
    response = client.get("/gateway/routes/resolve", params={"path": "/does-not-exist"})

    assert response.status_code == 200
    assert response.json()["matched"] is False
    assert response.json()["reason"] == "not_found"


def test_api_resolve_returns_matched_result(client: TestClient):
    response = client.get(
        "/gateway/routes/resolve", params={"path": "/notebooks", "method": "POST"}
    )

    assert response.status_code == 200
    assert response.json()["matched"] is True
