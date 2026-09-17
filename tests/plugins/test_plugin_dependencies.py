import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.plugin_dependencies import (
    CircularDependencyError,
    DependencyGraph,
    PluginDependency,
    PluginDependencyManager,
    UnsatisfiedDependencyError,
    get_plugin_dependency_manager,
    router as plugin_dependencies_router,
    satisfies,
)
from backend.plugins.plugin_registry import PluginRegistry


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry()


@pytest.fixture
def manager(registry: PluginRegistry) -> PluginDependencyManager:
    return PluginDependencyManager(registry)


@pytest.fixture
def client(manager: PluginDependencyManager) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_dependencies_router)
    app.dependency_overrides[get_plugin_dependency_manager] = lambda: manager
    return TestClient(app)


def test_satisfies_wildcard_always_true():
    assert satisfies("1.0.0", "*") is True
    assert satisfies("1.0.0", "") is True


def test_satisfies_exact_match():
    assert satisfies("1.2.3", "1.2.3") is True
    assert satisfies("1.2.4", "1.2.3") is False


def test_satisfies_range_constraint():
    assert satisfies("1.5.0", ">=1.0.0,<2.0.0") is True
    assert satisfies("2.0.0", ">=1.0.0,<2.0.0") is False
    assert satisfies("0.9.0", ">=1.0.0,<2.0.0") is False


def test_add_dependency_returns_edge(manager: PluginDependencyManager):
    dependency = manager.add_dependency("app", "auth-plugin", ">=1.0.0")

    assert isinstance(dependency, PluginDependency)
    assert dependency.plugin == "app"
    assert dependency.depends_on == "auth-plugin"


def test_add_dependency_rejects_self_dependency(manager: PluginDependencyManager):
    with pytest.raises(ValueError):
        manager.add_dependency("app", "app")


def test_get_dependencies_returns_direct_edges_only(manager: PluginDependencyManager):
    manager.add_dependency("app", "auth-plugin")
    manager.add_dependency("app", "logging-plugin")
    manager.add_dependency("auth-plugin", "crypto-lib")

    deps = {dependency.depends_on for dependency in manager.get_dependencies("app")}

    assert deps == {"auth-plugin", "logging-plugin"}


def test_get_dependencies_for_unknown_plugin_returns_empty(manager: PluginDependencyManager):
    assert manager.get_dependencies("does-not-exist") == []


def test_resolve_returns_transitive_dependencies_in_order(manager: PluginDependencyManager):
    manager.add_dependency("app", "auth-plugin")
    manager.add_dependency("auth-plugin", "crypto-lib")

    order = manager.resolve("app")

    assert order == ["crypto-lib", "auth-plugin", "app"]


def test_resolve_detects_direct_cycle(manager: PluginDependencyManager):
    manager.add_dependency("a", "b")
    manager.add_dependency("b", "a")

    with pytest.raises(CircularDependencyError):
        manager.resolve("a")


def test_resolve_detects_indirect_cycle(manager: PluginDependencyManager):
    manager.add_dependency("a", "b")
    manager.add_dependency("b", "c")
    manager.add_dependency("c", "a")

    with pytest.raises(CircularDependencyError):
        manager.resolve("a")


def test_load_order_orders_entire_graph(manager: PluginDependencyManager):
    manager.add_dependency("app", "auth-plugin")
    manager.add_dependency("app", "logging-plugin")
    manager.add_dependency("auth-plugin", "crypto-lib")

    order = manager.load_order()

    assert order.index("crypto-lib") < order.index("auth-plugin")
    assert order.index("auth-plugin") < order.index("app")
    assert order.index("logging-plugin") < order.index("app")
    assert set(order) == {"app", "auth-plugin", "logging-plugin", "crypto-lib"}


def test_load_order_raises_on_cycle(manager: PluginDependencyManager):
    manager.add_dependency("a", "b")
    manager.add_dependency("b", "a")

    with pytest.raises(CircularDependencyError):
        manager.load_order()


def test_get_graph_returns_snapshot(manager: PluginDependencyManager):
    manager.add_dependency("app", "auth-plugin", ">=1.0.0")

    graph = manager.get_graph()

    assert isinstance(graph, DependencyGraph)
    assert set(graph.nodes) == {"app", "auth-plugin"}
    assert graph.edges == (PluginDependency("app", "auth-plugin", ">=1.0.0"),)


def test_validate_passes_when_dependency_installed_and_compatible(
    manager: PluginDependencyManager, registry: PluginRegistry
):
    registry.register("auth-plugin", "1.5.0")
    manager.add_dependency("app", "auth-plugin", ">=1.0.0")

    assert manager.validate("app") is True


def test_validate_raises_when_dependency_not_installed(manager: PluginDependencyManager):
    manager.add_dependency("app", "auth-plugin", ">=1.0.0")

    with pytest.raises(UnsatisfiedDependencyError):
        manager.validate("app")


def test_validate_raises_when_version_incompatible(manager: PluginDependencyManager, registry: PluginRegistry):
    registry.register("auth-plugin", "0.5.0")
    manager.add_dependency("app", "auth-plugin", ">=1.0.0")

    with pytest.raises(UnsatisfiedDependencyError):
        manager.validate("app")


def test_validate_whole_graph_checks_every_edge(manager: PluginDependencyManager, registry: PluginRegistry):
    registry.register("auth-plugin", "1.0.0")
    manager.add_dependency("app", "auth-plugin", ">=1.0.0")
    manager.add_dependency("other-app", "missing-plugin")

    with pytest.raises(UnsatisfiedDependencyError):
        manager.validate()


def test_api_add_dependency_then_get(client: TestClient):
    response = client.post(
        "/plugins/dependencies",
        json={"plugin": "app", "depends_on": "auth-plugin", "version_constraint": ">=1.0.0"},
    )
    assert response.status_code == 201

    listed = client.get("/plugins/dependencies/app")
    assert listed.status_code == 200
    assert listed.json() == [
        {"plugin": "app", "depends_on": "auth-plugin", "version_constraint": ">=1.0.0"}
    ]


def test_api_add_self_dependency_returns_422(client: TestClient):
    response = client.post("/plugins/dependencies", json={"plugin": "app", "depends_on": "app"})

    assert response.status_code == 422


def test_api_get_dependencies_for_unknown_plugin_returns_empty_list(client: TestClient):
    response = client.get("/plugins/dependencies/does-not-exist")

    assert response.status_code == 200
    assert response.json() == []


def test_api_load_order(client: TestClient):
    client.post("/plugins/dependencies", json={"plugin": "app", "depends_on": "auth-plugin"})

    response = client.get("/plugins/load-order")

    assert response.status_code == 200
    order = response.json()
    assert order.index("auth-plugin") < order.index("app")


def test_api_load_order_returns_409_on_cycle(client: TestClient):
    client.post("/plugins/dependencies", json={"plugin": "a", "depends_on": "b"})
    client.post("/plugins/dependencies", json={"plugin": "b", "depends_on": "a"})

    response = client.get("/plugins/load-order")

    assert response.status_code == 409
