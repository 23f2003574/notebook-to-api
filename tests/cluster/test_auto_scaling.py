import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.auto_scaling import (
    AutoScalingEngine,
    ScalingDecision,
    ScalingPolicy,
    get_auto_scaling_engine,
    router as auto_scaling_router,
)
from backend.cluster.distributed_scheduler import DistributedScheduler
from backend.cluster.worker_discovery import WorkerDiscoveryService
from backend.cluster.worker_registry import WorkerMetadata, WorkerRegistry


def make_metadata(hostname: str = "node-a.local") -> WorkerMetadata:
    return WorkerMetadata(hostname=hostname, region="us-east-1", version="1.0.0")


@pytest.fixture
def registry() -> WorkerRegistry:
    return WorkerRegistry()


@pytest.fixture
def discovery(registry: WorkerRegistry) -> WorkerDiscoveryService:
    return WorkerDiscoveryService(registry, stale_after_seconds=300.0)


@pytest.fixture
def scheduler(discovery: WorkerDiscoveryService) -> DistributedScheduler:
    return DistributedScheduler(discovery)


@pytest.fixture
def policy() -> ScalingPolicy:
    return ScalingPolicy(
        min_workers=1,
        max_workers=3,
        scale_up_step=1,
        scale_down_step=1,
        scale_up_queue_threshold=2,
        scale_down_queue_threshold=0,
        scale_up_cpu_threshold=80.0,
        scale_down_cpu_threshold=20.0,
        cooldown_seconds=60.0,
    )


@pytest.fixture
def engine(registry: WorkerRegistry, discovery: WorkerDiscoveryService, scheduler: DistributedScheduler, policy: ScalingPolicy) -> AutoScalingEngine:
    return AutoScalingEngine(registry, discovery, scheduler, policy=policy)


@pytest.fixture
def client(engine: AutoScalingEngine) -> TestClient:
    app = FastAPI()
    app.include_router(auto_scaling_router)
    app.dependency_overrides[get_auto_scaling_engine] = lambda: engine
    return TestClient(app)


def test_evaluate_holds_when_within_normal_range(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())

    decision = engine.evaluate("parse", cpu_utilization=40.0)

    assert isinstance(decision, ScalingDecision)
    assert decision.action == "hold"
    assert decision.executed is False


def test_evaluate_scales_up_on_high_cpu(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())

    decision = engine.evaluate("parse", cpu_utilization=90.0)

    assert decision.action == "scale_up"
    assert decision.worker_delta == 1
    assert "cpu_utilization" in decision.triggers


def test_evaluate_scales_up_on_queue_length(
    registry: WorkerRegistry, scheduler: DistributedScheduler, engine: AutoScalingEngine
):
    registry.register("worker-1", ["parse"], make_metadata())
    for i in range(3):
        scheduler.schedule(f"job-{i}", "parse")
        scheduler.reserve(f"job-{i}")

    decision = engine.evaluate("parse")

    assert decision.action == "scale_up"
    assert "queue_length" in decision.triggers


def test_evaluate_respects_max_workers_cap(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())
    registry.register("worker-2", ["parse"], make_metadata())
    registry.register("worker-3", ["parse"], make_metadata())

    decision = engine.evaluate("parse", cpu_utilization=95.0)

    assert decision.action == "hold"
    assert "max_workers" in decision.reason


def test_evaluate_scales_down_on_low_utilization(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())
    registry.register("worker-2", ["parse"], make_metadata())

    decision = engine.evaluate("parse", cpu_utilization=5.0)

    assert decision.action == "scale_down"
    assert decision.worker_delta == -1


def test_evaluate_respects_min_workers_floor(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())

    decision = engine.evaluate("parse", cpu_utilization=5.0)

    assert decision.action == "hold"


def test_scale_up_registers_new_workers(registry: WorkerRegistry, engine: AutoScalingEngine):
    decision = engine.scale_up("parse", 2)

    assert decision.action == "scale_up"
    assert decision.worker_delta == 2
    assert decision.executed is True
    assert len(registry.list_workers(capability="parse")) == 2


def test_scale_up_rejects_non_positive_count(engine: AutoScalingEngine):
    with pytest.raises(ValueError):
        engine.scale_up("parse", 0)


def test_scale_down_removes_least_loaded_workers(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, engine: AutoScalingEngine
):
    registry.register("worker-busy", ["parse"], make_metadata())
    registry.register("worker-idle", ["parse"], make_metadata())
    discovery.set_load("worker-busy", 5)

    decision = engine.scale_down("parse", 1)

    assert decision.worker_delta == -1
    assert registry.get("worker-idle") is None
    assert registry.get("worker-busy") is not None


def test_scale_down_removes_at_most_available_workers(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())

    decision = engine.scale_down("parse", 5)

    assert decision.worker_delta == -1


def test_scale_down_rejects_non_positive_count(engine: AutoScalingEngine):
    with pytest.raises(ValueError):
        engine.scale_down("parse", -1)


def test_cooldown_blocks_repeated_scaling(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())
    engine.scale_up("parse", 1)

    decision = engine.evaluate("parse", cpu_utilization=95.0)

    assert decision.action == "hold"
    assert "cooldown" in decision.reason


def test_cooldown_does_not_block_when_expired(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, scheduler: DistributedScheduler
):
    zero_cooldown_policy = ScalingPolicy(min_workers=1, max_workers=3, cooldown_seconds=0.0)
    engine = AutoScalingEngine(registry, discovery, scheduler, policy=zero_cooldown_policy)
    registry.register("worker-1", ["parse"], make_metadata())
    engine.scale_up("parse", 1)

    decision = engine.evaluate("parse", cpu_utilization=95.0)

    assert decision.action == "scale_up"


def test_recommend_executes_scale_up_decision(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())

    decision = engine.recommend("parse", cpu_utilization=95.0)

    assert decision.executed is True
    assert len(registry.list_workers(capability="parse")) == 2


def test_recommend_does_not_execute_hold_decision(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())

    decision = engine.recommend("parse", cpu_utilization=40.0)

    assert decision.executed is False
    assert len(registry.list_workers(capability="parse")) == 1


def test_history_records_every_evaluation(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())
    engine.evaluate("parse", cpu_utilization=10.0)
    engine.evaluate("parse", cpu_utilization=20.0)

    history = engine.get_history()

    assert len(history) == 2


def test_history_filters_by_capability(registry: WorkerRegistry, engine: AutoScalingEngine):
    registry.register("worker-1", ["parse"], make_metadata())
    registry.register("worker-2", ["export"], make_metadata())
    engine.evaluate("parse")
    engine.evaluate("export")

    parse_history = engine.get_history(capability="parse")

    assert len(parse_history) == 1
    assert parse_history[0].capability == "parse"


def test_api_evaluate(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.post("/cluster/scaling/evaluate", json={"capability": "parse", "cpu_utilization": 90.0})

    assert response.status_code == 200
    assert response.json()["action"] == "scale_up"


def test_api_evaluate_missing_field_returns_422(client: TestClient):
    response = client.post("/cluster/scaling/evaluate", json={})

    assert response.status_code == 422


def test_api_scale_up(client: TestClient):
    response = client.post("/cluster/scaling/up", json={"capability": "parse", "count": 2})

    assert response.status_code == 200
    assert response.json()["worker_delta"] == 2


def test_api_scale_down(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.post("/cluster/scaling/down", json={"capability": "parse", "count": 1})

    assert response.status_code == 200
    assert response.json()["worker_delta"] == -1


def test_api_scale_up_invalid_count_returns_422(client: TestClient):
    response = client.post("/cluster/scaling/up", json={"capability": "parse", "count": 0})

    assert response.status_code == 422


def test_api_history(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    client.post("/cluster/scaling/evaluate", json={"capability": "parse"})

    response = client.get("/cluster/scaling/history")

    assert response.status_code == 200
    assert len(response.json()) == 1
