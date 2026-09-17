import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.load_balancer import (
    BackendAlreadyRegisteredError,
    BackendNode,
    InvalidStrategyError,
    LoadBalancer,
    LoadBalancerState,
    NoHealthyBackendError,
    UnknownBackendError,
    get_load_balancer,
    router as load_balancer_router,
)


@pytest.fixture
def balancer() -> LoadBalancer:
    return LoadBalancer()


@pytest.fixture
def client(balancer: LoadBalancer) -> TestClient:
    app = FastAPI()
    app.include_router(load_balancer_router)
    app.dependency_overrides[get_load_balancer] = lambda: balancer
    return TestClient(app)


def test_register_backend_creates_node(balancer: LoadBalancer):
    node = balancer.register_backend("api-1", "http://api-1:8000")

    assert isinstance(node, BackendNode)
    assert node.name == "api-1"
    assert node.healthy is True


def test_register_backend_rejects_duplicate(balancer: LoadBalancer):
    balancer.register_backend("api-1", "http://api-1:8000")

    with pytest.raises(BackendAlreadyRegisteredError):
        balancer.register_backend("api-1", "http://api-1:8000")


def test_register_backend_rejects_non_positive_weight(balancer: LoadBalancer):
    with pytest.raises(ValueError):
        balancer.register_backend("api-1", "http://api-1:8000", weight=0)


def test_constructor_rejects_unknown_strategy():
    with pytest.raises(InvalidStrategyError):
        LoadBalancer(strategy="ip-hash")


def test_select_raises_when_no_backends(balancer: LoadBalancer):
    with pytest.raises(NoHealthyBackendError):
        balancer.select()


def test_select_raises_when_all_unhealthy(balancer: LoadBalancer):
    balancer.register_backend("api-1", "http://api-1:8000")
    balancer.mark_unhealthy("api-1", healthy=False)

    with pytest.raises(NoHealthyBackendError):
        balancer.select()


def test_mark_unhealthy_unknown_backend_raises(balancer: LoadBalancer):
    with pytest.raises(UnknownBackendError):
        balancer.mark_unhealthy("does-not-exist")


# --- round robin ---


def test_round_robin_cycles_through_backends():
    balancer = LoadBalancer(strategy="round_robin")
    balancer.register_backend("api-1", "http://api-1")
    balancer.register_backend("api-2", "http://api-2")

    selected = [balancer.select().name for _ in range(4)]

    assert selected == ["api-1", "api-2", "api-1", "api-2"]


def test_round_robin_skips_unhealthy_backend():
    balancer = LoadBalancer(strategy="round_robin")
    balancer.register_backend("api-1", "http://api-1")
    balancer.register_backend("api-2", "http://api-2")
    balancer.mark_unhealthy("api-2", healthy=False)

    selected = [balancer.select().name for _ in range(3)]

    assert selected == ["api-1", "api-1", "api-1"]


# --- least connections ---


def test_least_connections_prefers_idle_backend():
    balancer = LoadBalancer(strategy="least_connections")
    balancer.register_backend("api-1", "http://api-1")
    balancer.register_backend("api-2", "http://api-2")

    first = balancer.select()
    assert first.name == "api-1"

    second = balancer.select()
    assert second.name == "api-2"

    balancer.release_backend("api-1")
    third = balancer.select()
    assert third.name == "api-1"


# --- weighted round robin ---


def test_weighted_round_robin_favors_higher_weight():
    balancer = LoadBalancer(strategy="weighted_round_robin")
    balancer.register_backend("api-1", "http://api-1", weight=2)
    balancer.register_backend("api-2", "http://api-2", weight=1)

    selected = [balancer.select().name for _ in range(6)]

    assert selected.count("api-1") == 4
    assert selected.count("api-2") == 2


# --- random ---


def test_random_strategy_only_selects_registered_backends():
    import random

    balancer = LoadBalancer(strategy="random", random_source=random.Random(42))
    balancer.register_backend("api-1", "http://api-1")
    balancer.register_backend("api-2", "http://api-2")

    for _ in range(10):
        node = balancer.select()
        assert node.name in {"api-1", "api-2"}


# --- failover ---


def test_failover_routes_around_newly_unhealthy_backend():
    balancer = LoadBalancer(strategy="round_robin")
    balancer.register_backend("api-1", "http://api-1")
    balancer.register_backend("api-2", "http://api-2")

    assert balancer.select().name == "api-1"
    balancer.mark_unhealthy("api-1", healthy=False)

    assert balancer.select().name == "api-2"
    assert balancer.select().name == "api-2"


def test_backend_recovers_after_marked_healthy_again():
    balancer = LoadBalancer(strategy="round_robin")
    balancer.register_backend("api-1", "http://api-1")
    balancer.mark_unhealthy("api-1", healthy=False)

    with pytest.raises(NoHealthyBackendError):
        balancer.select()

    balancer.mark_unhealthy("api-1", healthy=True)

    assert balancer.select().name == "api-1"


# --- rebalance / state ---


def test_rebalance_reports_state(balancer: LoadBalancer):
    balancer.register_backend("api-1", "http://api-1")
    balancer.register_backend("api-2", "http://api-2")
    balancer.mark_unhealthy("api-2", healthy=False)
    balancer.select()

    state = balancer.rebalance()

    assert isinstance(state, LoadBalancerState)
    assert state.total_backends == 2
    assert state.healthy_backends == 1
    assert state.total_selections == 1


# --- API ---


def test_api_register_and_list_backends(client: TestClient):
    response = client.post("/gateway/backends", json={"name": "api-1", "address": "http://api-1"})
    assert response.status_code == 201
    assert response.json()["name"] == "api-1"

    listed = client.get("/gateway/backends")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/gateway/backends", json={"name": "api-1", "address": "http://api-1"})
    response = client.post("/gateway/backends", json={"name": "api-1", "address": "http://api-1"})

    assert response.status_code == 409


def test_api_register_invalid_weight_returns_422(client: TestClient):
    response = client.post(
        "/gateway/backends", json={"name": "api-1", "address": "http://api-1", "weight": 0}
    )

    assert response.status_code == 422


def test_api_health_update_marks_unhealthy(client: TestClient):
    client.post("/gateway/backends", json={"name": "api-1", "address": "http://api-1"})

    response = client.post("/gateway/backends/api-1/health", json={"healthy": False})

    assert response.status_code == 200
    assert response.json()["healthy"] is False


def test_api_health_update_unknown_backend_returns_404(client: TestClient):
    response = client.post("/gateway/backends/does-not-exist/health", json={"healthy": False})

    assert response.status_code == 404


def test_api_load_balancer_status(client: TestClient):
    client.post("/gateway/backends", json={"name": "api-1", "address": "http://api-1"})

    response = client.get("/gateway/load-balancer/status")

    assert response.status_code == 200
    body = response.json()
    assert body["total_backends"] == 1
    assert body["healthy_backends"] == 1
