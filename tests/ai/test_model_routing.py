import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.model_registry import ModelMetadata, ModelRegistry, get_model_registry
from backend.ai.model_routing import (
    InvalidStrategyError,
    ModelRoutingEngine,
    NoAvailableModelError,
    RouteAlreadyRegisteredError,
    RoutingDecision,
    RoutingRule,
    UnknownRouteError,
    get_model_routing_engine,
    router as model_routing_router,
)


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry()


@pytest.fixture
def engine() -> ModelRoutingEngine:
    return ModelRoutingEngine()


@pytest.fixture
def client(registry: ModelRegistry, engine: ModelRoutingEngine) -> TestClient:
    app = FastAPI()
    app.include_router(model_routing_router)
    app.dependency_overrides[get_model_registry] = lambda: registry
    app.dependency_overrides[get_model_routing_engine] = lambda: engine
    return TestClient(app)


def test_register_route_creates_rule(engine: ModelRoutingEngine):
    rule = engine.register_route("chat", "priority", candidates=["gpt-a", "gpt-b"])

    assert isinstance(rule, RoutingRule)
    assert rule.strategy == "priority"
    assert rule.candidates == ("gpt-a", "gpt-b")


def test_register_route_rejects_unsupported_strategy(engine: ModelRoutingEngine):
    with pytest.raises(InvalidStrategyError):
        engine.register_route("chat", "bogus", candidates=["gpt-a"])


def test_register_route_rejects_priority_without_candidates(engine: ModelRoutingEngine):
    with pytest.raises(ValueError):
        engine.register_route("chat", "priority")


def test_register_route_rejects_capability_without_capability(engine: ModelRoutingEngine):
    with pytest.raises(ValueError):
        engine.register_route("chat", "capability")


def test_register_route_rejects_duplicate(engine: ModelRoutingEngine):
    engine.register_route("chat", "priority", candidates=["gpt-a"])

    with pytest.raises(RouteAlreadyRegisteredError):
        engine.register_route("chat", "priority", candidates=["gpt-b"])


def test_select_priority_strategy_picks_first_registered_candidate(
    registry: ModelRegistry, engine: ModelRoutingEngine
):
    registry.register("gpt-b", "1.0.0")
    engine.register_route("chat", "priority", candidates=["gpt-a", "gpt-b"])

    decision = engine.select("chat", registry=registry)

    assert isinstance(decision, RoutingDecision)
    assert decision.selected_model == "gpt-b"
    assert decision.is_fallback is False


def test_select_capability_strategy_matches_registered_capability(
    registry: ModelRegistry, engine: ModelRoutingEngine
):
    registry.register("gpt-embed", "1.0.0", ModelMetadata(capabilities=("embeddings",)))
    registry.register("gpt-chat", "1.0.0", ModelMetadata(capabilities=("chat",)))
    engine.register_route("embed-task", "capability", capability="embeddings")

    decision = engine.select("embed-task", registry=registry)

    assert decision.selected_model == "gpt-embed"


def test_select_latency_strategy_picks_lowest_latency(registry: ModelRegistry, engine: ModelRoutingEngine):
    registry.register("gpt-slow", "1.0.0", ModelMetadata(latency_ms=500))
    registry.register("gpt-fast", "1.0.0", ModelMetadata(latency_ms=50))
    engine.register_route("chat", "latency", candidates=["gpt-slow", "gpt-fast"])

    decision = engine.select("chat", registry=registry)

    assert decision.selected_model == "gpt-fast"


def test_select_weighted_strategy_is_deterministic_with_random_value(
    registry: ModelRegistry, engine: ModelRoutingEngine
):
    registry.register("gpt-a", "1.0.0", ModelMetadata(weight=1.0))
    registry.register("gpt-b", "1.0.0", ModelMetadata(weight=3.0))
    engine.register_route("chat", "weighted", candidates=["gpt-a", "gpt-b"])

    low = engine.select("chat", registry=registry, random_value=0.0)
    high = engine.select("chat", registry=registry, random_value=0.99)

    assert low.selected_model == "gpt-a"
    assert high.selected_model == "gpt-b"


def test_select_unknown_route_raises(registry: ModelRegistry, engine: ModelRoutingEngine):
    with pytest.raises(UnknownRouteError):
        engine.select("does-not-exist", registry=registry)


def test_select_falls_back_when_no_candidate_registered(registry: ModelRegistry, engine: ModelRoutingEngine):
    registry.register("gpt-fallback", "1.0.0")
    engine.register_route("chat", "priority", candidates=["gpt-a", "gpt-b"], fallback="gpt-fallback")

    decision = engine.select("chat", registry=registry)

    assert decision.selected_model == "gpt-fallback"
    assert decision.is_fallback is True


def test_fallback_raises_when_no_fallback_available(registry: ModelRegistry, engine: ModelRoutingEngine):
    engine.register_route("chat", "priority", candidates=["gpt-a"])

    with pytest.raises(NoAvailableModelError):
        engine.select("chat", registry=registry)


def test_route_stats_tracks_selections_and_fallbacks(registry: ModelRegistry, engine: ModelRoutingEngine):
    registry.register("gpt-a", "1.0.0")
    registry.register("gpt-fallback", "1.0.0")
    engine.register_route("chat", "priority", candidates=["gpt-a", "gpt-missing"], fallback="gpt-fallback")

    engine.select("chat", registry=registry)
    registry.remove("gpt-a")
    engine.select("chat", registry=registry)

    stats = engine.route_stats("chat")

    assert stats["total"] == 2
    assert stats["fallback_count"] == 1
    assert stats["selections"]["gpt-a"] == 1
    assert stats["selections"]["gpt-fallback"] == 1


def test_route_stats_unknown_route_raises(engine: ModelRoutingEngine):
    with pytest.raises(UnknownRouteError):
        engine.route_stats("does-not-exist")


def test_list_routes_returns_all(engine: ModelRoutingEngine):
    engine.register_route("chat", "priority", candidates=["gpt-a"])
    engine.register_route("embed", "priority", candidates=["gpt-b"])

    listed = engine.list_routes()

    assert [rule.route_name for rule in listed] == ["chat", "embed"]


def test_api_register_and_list_routes(client: TestClient):
    response = client.post(
        "/ai/routing", json={"route_name": "chat", "strategy": "priority", "candidates": ["gpt-a"]}
    )
    assert response.status_code == 201
    assert response.json()["route_name"] == "chat"

    listed = client.get("/ai/routing")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_invalid_strategy_returns_422(client: TestClient):
    response = client.post(
        "/ai/routing", json={"route_name": "chat", "strategy": "bogus", "candidates": ["gpt-a"]}
    )

    assert response.status_code == 422


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/ai/routing", json={"route_name": "chat", "strategy": "priority", "candidates": ["gpt-a"]})
    response = client.post(
        "/ai/routing", json={"route_name": "chat", "strategy": "priority", "candidates": ["gpt-b"]}
    )

    assert response.status_code == 409


def test_api_select_route(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    client.post("/ai/routing", json={"route_name": "chat", "strategy": "priority", "candidates": ["gpt-a"]})

    response = client.post("/ai/routing/select", json={"route_name": "chat"})

    assert response.status_code == 200
    assert response.json()["selected_model"] == "gpt-a"


def test_api_select_unknown_route_returns_404(client: TestClient):
    response = client.post("/ai/routing/select", json={"route_name": "does-not-exist"})

    assert response.status_code == 404


def test_api_select_no_available_model_returns_503(client: TestClient):
    client.post("/ai/routing", json={"route_name": "chat", "strategy": "priority", "candidates": ["gpt-a"]})

    response = client.post("/ai/routing/select", json={"route_name": "chat"})

    assert response.status_code == 503


def test_api_route_stats(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    client.post("/ai/routing", json={"route_name": "chat", "strategy": "priority", "candidates": ["gpt-a"]})
    client.post("/ai/routing/select", json={"route_name": "chat"})

    response = client.get("/ai/routing/stats", params={"route": "chat"})

    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_api_route_stats_all_routes(client: TestClient, registry: ModelRegistry):
    registry.register("gpt-a", "1.0.0")
    client.post("/ai/routing", json={"route_name": "chat", "strategy": "priority", "candidates": ["gpt-a"]})
    client.post("/ai/routing/select", json={"route_name": "chat"})

    response = client.get("/ai/routing/stats")

    assert response.status_code == 200
    assert "chat" in response.json()
