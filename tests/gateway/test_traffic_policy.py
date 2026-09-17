import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.traffic_policy import (
    PolicyAlreadyRegisteredError,
    PolicyResult,
    TrafficPolicy,
    TrafficPolicyEngine,
    UnknownPolicyError,
    get_policy_engine,
    router as policy_router,
)


@pytest.fixture
def engine() -> TrafficPolicyEngine:
    return TrafficPolicyEngine()


@pytest.fixture
def client(engine: TrafficPolicyEngine) -> TestClient:
    app = FastAPI()
    app.include_router(policy_router)
    app.dependency_overrides[get_policy_engine] = lambda: engine
    return TestClient(app)


def always_matches(name: str, action: str = "deny"):
    def handler(context: dict) -> PolicyResult:
        return PolicyResult(policy=name, matched=True, action=action, reason="always matches")

    return handler


def never_matches(name: str):
    def handler(context: dict) -> PolicyResult:
        return PolicyResult(policy=name, matched=False, action="skip", reason="never matches")

    return handler


def test_register_policy_creates_policy(engine: TrafficPolicyEngine):
    policy = engine.register_policy("always-deny", always_matches("always-deny"))

    assert isinstance(policy, TrafficPolicy)
    assert policy.name == "always-deny"
    assert policy.enabled is True


def test_register_policy_rejects_duplicate_name(engine: TrafficPolicyEngine):
    engine.register_policy("always-deny", always_matches("always-deny"))

    with pytest.raises(PolicyAlreadyRegisteredError):
        engine.register_policy("always-deny", always_matches("always-deny"))


def test_register_policy_requires_name(engine: TrafficPolicyEngine):
    with pytest.raises(ValueError):
        engine.register_policy("", always_matches("x"))


# --- enable / disable ---


def test_disable_prevents_policy_from_matching(engine: TrafficPolicyEngine):
    engine.register_policy("always-deny", always_matches("always-deny"))
    engine.disable("always-deny")

    result = engine.evaluate({})

    assert result is None


def test_enable_restores_policy_matching(engine: TrafficPolicyEngine):
    engine.register_policy("always-deny", always_matches("always-deny"))
    engine.disable("always-deny")
    engine.enable("always-deny")

    result = engine.evaluate({})

    assert result is not None
    assert result.policy == "always-deny"


def test_enable_unknown_policy_raises(engine: TrafficPolicyEngine):
    with pytest.raises(UnknownPolicyError):
        engine.enable("does-not-exist")


def test_disable_unknown_policy_raises(engine: TrafficPolicyEngine):
    with pytest.raises(UnknownPolicyError):
        engine.disable("does-not-exist")


# --- priority resolution ---


def test_evaluate_returns_first_matching_policy_by_priority(engine: TrafficPolicyEngine):
    engine.register_policy("low-priority", always_matches("low-priority", "shape"), priority=10)
    engine.register_policy("high-priority", always_matches("high-priority", "deny"), priority=0)

    result = engine.evaluate({})

    assert result.policy == "high-priority"


def test_evaluate_skips_non_matching_higher_priority_policy(engine: TrafficPolicyEngine):
    engine.register_policy("high-priority", never_matches("high-priority"), priority=0)
    engine.register_policy("low-priority", always_matches("low-priority", "deny"), priority=10)

    result = engine.evaluate({})

    assert result.policy == "low-priority"


def test_evaluate_returns_none_when_nothing_matches(engine: TrafficPolicyEngine):
    engine.register_policy("policy-a", never_matches("policy-a"))
    engine.register_policy("policy-b", never_matches("policy-b"))

    assert engine.evaluate({}) is None


def test_evaluate_named_policy_ignores_priority(engine: TrafficPolicyEngine):
    engine.register_policy("high-priority", never_matches("high-priority"), priority=0)
    engine.register_policy("low-priority", always_matches("low-priority", "deny"), priority=10)

    result = engine.evaluate({}, name="low-priority")

    assert result.policy == "low-priority"
    assert result.matched is True


def test_evaluate_named_unknown_policy_raises(engine: TrafficPolicyEngine):
    with pytest.raises(UnknownPolicyError):
        engine.evaluate({}, name="does-not-exist")


def test_evaluate_named_disabled_policy_returns_skip_result(engine: TrafficPolicyEngine):
    engine.register_policy("always-deny", always_matches("always-deny"))
    engine.disable("always-deny")

    result = engine.evaluate({}, name="always-deny")

    assert result.matched is False
    assert result.action == "skip"


# --- built-in: geo routing ---


def test_geo_routing_routes_by_country():
    from backend.gateway.traffic_policy import _build_geo_routing

    handler = _build_geo_routing("geo", {"field": "country", "routes": {"US": "us-east"}, "default": "eu-west"})

    us_result = handler({"country": "US"})
    other_result = handler({"country": "IN"})

    assert us_result.metadata["target"] == "us-east"
    assert other_result.metadata["target"] == "eu-west"


# --- built-in: maintenance mode ---


def test_maintenance_mode_denies_when_active():
    from backend.gateway.traffic_policy import _build_maintenance_mode

    handler = _build_maintenance_mode("maintenance", {"active": True})

    result = handler({"path": "/notebooks"})

    assert result.matched is True
    assert result.action == "deny"


def test_maintenance_mode_allows_allow_listed_path():
    from backend.gateway.traffic_policy import _build_maintenance_mode

    handler = _build_maintenance_mode("maintenance", {"active": True, "allowed_paths": ["/health"]})

    result = handler({"path": "/health"})

    assert result.matched is False


def test_maintenance_mode_inactive_does_not_match():
    from backend.gateway.traffic_policy import _build_maintenance_mode

    handler = _build_maintenance_mode("maintenance", {"active": False})

    result = handler({"path": "/notebooks"})

    assert result.matched is False


# --- built-in: canary routing ---


def test_canary_routing_is_deterministic_for_same_key():
    from backend.gateway.traffic_policy import _build_canary_routing

    handler = _build_canary_routing(
        "canary", {"percentage": 50, "canary_target": "api-canary", "stable_target": "api-stable"}
    )

    first = handler({"client_id": "user-42"})
    second = handler({"client_id": "user-42"})

    assert first.metadata["target"] == second.metadata["target"]


def test_canary_routing_zero_percent_always_stable():
    from backend.gateway.traffic_policy import _build_canary_routing

    handler = _build_canary_routing(
        "canary", {"percentage": 0, "canary_target": "api-canary", "stable_target": "api-stable"}
    )

    for client_id in ["a", "b", "c", "d", "e"]:
        result = handler({"client_id": client_id})
        assert result.metadata["target"] == "api-stable"


# --- built-in: traffic shaping ---


def test_traffic_shaping_matches_affected_path():
    from backend.gateway.traffic_policy import _build_traffic_shaping

    handler = _build_traffic_shaping("shaping", {"affected_paths": ["/heavy"], "delay_ms": 250})

    result = handler({"path": "/heavy/report"})

    assert result.matched is True
    assert result.metadata["delay_ms"] == 250


def test_traffic_shaping_ignores_unaffected_path():
    from backend.gateway.traffic_policy import _build_traffic_shaping

    handler = _build_traffic_shaping("shaping", {"affected_paths": ["/heavy"], "delay_ms": 250})

    result = handler({"path": "/light"})

    assert result.matched is False


# --- API ---


def test_api_register_and_list_policy(client: TestClient):
    response = client.post(
        "/gateway/policies",
        json={"type": "maintenance_mode", "config": {"active": True}},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "maintenance_mode"

    listed = client.get("/gateway/policies")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_api_register_unknown_type_returns_422(client: TestClient):
    response = client.post("/gateway/policies", json={"type": "does-not-exist"})

    assert response.status_code == 422


def test_api_register_duplicate_returns_409(client: TestClient):
    client.post("/gateway/policies", json={"type": "maintenance_mode"})
    response = client.post("/gateway/policies", json={"type": "maintenance_mode"})

    assert response.status_code == 409


def test_api_evaluate_policy(client: TestClient):
    client.post(
        "/gateway/policies",
        json={"type": "maintenance_mode", "name": "maint", "config": {"active": True}},
    )

    response = client.post("/gateway/policies/maint/evaluate", json={"context": {"path": "/notebooks"}})

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["action"] == "deny"


def test_api_evaluate_unknown_policy_returns_404(client: TestClient):
    response = client.post("/gateway/policies/does-not-exist/evaluate", json={"context": {}})

    assert response.status_code == 404
