import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.route_registry import (
    InvalidMethodError,
    Route,
    RouteAlreadyRegisteredError,
    RouteMetadata,
    RouteNotFoundError,
    RouteRegistry,
    get_route_registry,
    router as route_registry_router,
)


@pytest.fixture
def registry() -> RouteRegistry:
    return RouteRegistry()


@pytest.fixture
def client(registry: RouteRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(route_registry_router)
    app.dependency_overrides[get_route_registry] = lambda: registry
    return TestClient(app)


def test_register_creates_route(registry: RouteRegistry):
    route = registry.register(
        "/notebooks", ["GET", "post"], RouteMetadata(description="Notebook collection", owner="alice")
    )

    assert isinstance(route, Route)
    assert route.path == "/notebooks"
    assert route.methods == ("GET", "POST")
    assert route.metadata.owner == "alice"


def test_register_rejects_empty_path(registry: RouteRegistry):
    with pytest.raises(ValueError):
        registry.register("", ["GET"])


def test_register_rejects_empty_methods(registry: RouteRegistry):
    with pytest.raises(ValueError):
        registry.register("/notebooks", [])


def test_register_rejects_invalid_method(registry: RouteRegistry):
    with pytest.raises(InvalidMethodError):
        registry.register("/notebooks", ["FETCH"])


def test_register_rejects_duplicate_path(registry: RouteRegistry):
    registry.register("/notebooks", ["GET"])

    with pytest.raises(RouteAlreadyRegisteredError):
        registry.register("/notebooks", ["GET"])


def test_resolve_returns_registered_route(registry: RouteRegistry):
    registry.register("/notebooks", ["GET"])

    assert registry.resolve("/notebooks").path == "/notebooks"


def test_resolve_unknown_path_raises(registry: RouteRegistry):
    with pytest.raises(RouteNotFoundError):
        registry.resolve("/does-not-exist")


def test_resolve_validates_method(registry: RouteRegistry):
    registry.register("/notebooks", ["GET"])

    with pytest.raises(InvalidMethodError):
        registry.resolve("/notebooks", method="POST")


def test_resolve_allows_matching_method(registry: RouteRegistry):
    registry.register("/notebooks", ["GET", "POST"])

    assert registry.resolve("/notebooks", method="post").path == "/notebooks"


def test_list_routes_returns_all_registered(registry: RouteRegistry):
    registry.register("/notebooks", ["GET"])
    registry.register("/exports", ["POST"])

    listed = {route.path for route in registry.list_routes()}

    assert listed == {"/notebooks", "/exports"}


def test_list_routes_filters_by_tag(registry: RouteRegistry):
    registry.register("/notebooks", ["GET"], RouteMetadata(tags=("core",)))
    registry.register("/exports", ["POST"], RouteMetadata(tags=("export",)))

    listed = registry.list_routes(tag="export")

    assert [route.path for route in listed] == ["/exports"]


def test_unregister_removes_route(registry: RouteRegistry):
    registry.register("/notebooks", ["GET"])

    registry.unregister("/notebooks")

    with pytest.raises(RouteNotFoundError):
        registry.resolve("/notebooks")


def test_unregister_clears_tag_index(registry: RouteRegistry):
    registry.register("/notebooks", ["GET"], RouteMetadata(tags=("core",)))

    registry.unregister("/notebooks")

    assert registry.list_routes(tag="core") == []


def test_unregister_unknown_path_raises(registry: RouteRegistry):
    with pytest.raises(RouteNotFoundError):
        registry.unregister("/does-not-exist")


def test_api_register_and_list(client: TestClient):
    response = client.post(
        "/gateway/routes",
        json={"path": "/notebooks", "methods": ["GET"], "metadata": {"owner": "alice"}},
    )
    assert response.status_code == 201
    assert response.json()["path"] == "/notebooks"

    listed = client.get("/gateway/routes")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/gateway/routes", json={"path": "/notebooks", "methods": ["GET"]})
    response = client.post("/gateway/routes", json={"path": "/notebooks", "methods": ["GET"]})

    assert response.status_code == 409


def test_api_register_invalid_method_returns_422(client: TestClient):
    response = client.post("/gateway/routes", json={"path": "/notebooks", "methods": ["FETCH"]})

    assert response.status_code == 422


def test_api_get_unknown_route_returns_404(client: TestClient):
    response = client.get("/gateway/routes/does-not-exist")

    assert response.status_code == 404


def test_api_get_returns_route(client: TestClient):
    client.post("/gateway/routes", json={"path": "/notebooks", "methods": ["GET"]})

    response = client.get("/gateway/routes/notebooks")

    assert response.status_code == 200
    assert response.json()["path"] == "/notebooks"


def test_api_delete_removes_route(client: TestClient):
    client.post("/gateway/routes", json={"path": "/notebooks", "methods": ["GET"]})

    response = client.delete("/gateway/routes/notebooks")
    assert response.status_code == 204

    assert client.get("/gateway/routes/notebooks").status_code == 404
