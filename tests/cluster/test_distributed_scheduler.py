import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.distributed_scheduler import (
    DistributedScheduler,
    SchedulingPlan,
    get_distributed_scheduler,
    router as distributed_scheduler_router,
)
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
def client(scheduler: DistributedScheduler) -> TestClient:
    app = FastAPI()
    app.include_router(distributed_scheduler_router)
    app.dependency_overrides[get_distributed_scheduler] = lambda: scheduler
    return TestClient(app)


def test_schedule_least_loaded_prefers_idle_worker(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, scheduler: DistributedScheduler
):
    registry.register("worker-busy", ["parse"], make_metadata())
    registry.register("worker-idle", ["parse"], make_metadata())
    discovery.set_load("worker-busy", 5)

    plan = scheduler.schedule("job-1", "parse", policy="least_loaded")

    assert isinstance(plan, SchedulingPlan)
    assert plan.decision.worker_id == "worker-idle"


def test_schedule_capability_aware_prefers_specialized_worker(
    registry: WorkerRegistry, scheduler: DistributedScheduler
):
    registry.register("worker-generalist", ["parse", "export", "train"], make_metadata())
    registry.register("worker-specialist", ["parse"], make_metadata())

    plan = scheduler.schedule("job-1", "parse", policy="capability_aware")

    assert plan.decision.worker_id == "worker-specialist"


def test_schedule_priority_high_priority_uses_least_loaded(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, scheduler: DistributedScheduler
):
    registry.register("worker-a", ["parse"], make_metadata())
    registry.register("worker-b", ["parse"], make_metadata())
    discovery.set_load("worker-a", 3)

    plan = scheduler.schedule("job-1", "parse", policy="priority", priority=9)

    assert plan.decision.worker_id == "worker-b"


def test_schedule_affinity_targets_preferred_worker(registry: WorkerRegistry, scheduler: DistributedScheduler):
    registry.register("worker-a", ["parse"], make_metadata())
    registry.register("worker-b", ["parse"], make_metadata())

    plan = scheduler.schedule("job-1", "parse", policy="affinity", affinity_worker_id="worker-b")

    assert plan.decision.worker_id == "worker-b"
    assert "affinity" in plan.decision.reason


def test_schedule_affinity_falls_back_when_target_unavailable(
    registry: WorkerRegistry, scheduler: DistributedScheduler
):
    registry.register("worker-a", ["parse"], make_metadata())

    plan = scheduler.schedule("job-1", "parse", policy="affinity", affinity_worker_id="worker-missing")

    assert plan.decision.worker_id == "worker-a"
    assert "fell back" in plan.decision.reason


def test_schedule_no_candidates_returns_null_decision(scheduler: DistributedScheduler):
    plan = scheduler.schedule("job-1", "parse")

    assert plan.decision.worker_id is None
    assert plan.decision.score == float("inf")


def test_schedule_rejects_unsupported_policy(registry: WorkerRegistry, scheduler: DistributedScheduler):
    registry.register("worker-1", ["parse"], make_metadata())

    with pytest.raises(ValueError):
        scheduler.schedule("job-1", "parse", policy="magic")


def test_reserve_holds_capacity_for_planned_worker(registry: WorkerRegistry, scheduler: DistributedScheduler):
    registry.register("worker-1", ["parse"], make_metadata())
    scheduler.schedule("job-1", "parse")

    reserved = scheduler.reserve("job-1")

    assert reserved is True
    assert scheduler.stats()["reservations_active"] == 1


def test_reserve_without_schedule_raises(scheduler: DistributedScheduler):
    with pytest.raises(KeyError):
        scheduler.reserve("does-not-exist")


def test_reserve_twice_returns_false(registry: WorkerRegistry, scheduler: DistributedScheduler):
    registry.register("worker-1", ["parse"], make_metadata())
    scheduler.schedule("job-1", "parse")
    scheduler.reserve("job-1")

    assert scheduler.reserve("job-1") is False


def test_reserve_with_unplaceable_plan_returns_false(scheduler: DistributedScheduler):
    scheduler.schedule("job-1", "parse")

    assert scheduler.reserve("job-1") is False


def test_release_frees_reservation(registry: WorkerRegistry, scheduler: DistributedScheduler):
    registry.register("worker-1", ["parse"], make_metadata())
    scheduler.schedule("job-1", "parse")
    scheduler.reserve("job-1")

    released = scheduler.release("job-1")

    assert released is True
    assert scheduler.stats()["reservations_active"] == 0


def test_release_without_reservation_returns_false(scheduler: DistributedScheduler):
    assert scheduler.release("does-not-exist") is False


def test_effective_load_counts_reservations(registry: WorkerRegistry, discovery: WorkerDiscoveryService, scheduler: DistributedScheduler):
    registry.register("worker-a", ["parse"], make_metadata())
    registry.register("worker-b", ["parse"], make_metadata())
    scheduler.schedule("job-1", "parse", policy="least_loaded")
    scheduler.reserve("job-1")

    plan = scheduler.schedule("job-2", "parse", policy="least_loaded")

    assert plan.decision.worker_id == "worker-b"


def test_rebalance_moves_job_off_worker_that_went_offline(
    registry: WorkerRegistry, scheduler: DistributedScheduler
):
    registry.register("worker-1", ["parse"], make_metadata())
    scheduler.schedule("job-1", "parse")
    scheduler.reserve("job-1")
    registry.register("worker-2", ["parse"], make_metadata())
    registry.set_status("worker-1", "offline")

    moved = scheduler.rebalance()

    assert len(moved) == 1
    assert moved[0].decision.worker_id == "worker-2"
    assert scheduler.get_plan("job-1").decision.worker_id == "worker-2"


def test_rebalance_moves_job_to_less_loaded_worker(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, scheduler: DistributedScheduler
):
    registry.register("worker-1", ["parse"], make_metadata())
    scheduler.schedule("job-1", "parse", policy="capability_aware")
    scheduler.reserve("job-1")
    registry.register("worker-2", ["parse"], make_metadata())
    discovery.set_load("worker-1", 10)

    moved = scheduler.rebalance()

    assert len(moved) == 1
    assert moved[0].decision.worker_id == "worker-2"


def test_rebalance_leaves_balanced_jobs_untouched(registry: WorkerRegistry, scheduler: DistributedScheduler):
    registry.register("worker-1", ["parse"], make_metadata())
    scheduler.schedule("job-1", "parse")
    scheduler.reserve("job-1")

    moved = scheduler.rebalance()

    assert moved == []


def test_get_plan_missing_job_returns_none(scheduler: DistributedScheduler):
    assert scheduler.get_plan("does-not-exist") is None


def test_stats_tracks_scheduled_and_rebalanced_counts(registry: WorkerRegistry, scheduler: DistributedScheduler):
    registry.register("worker-1", ["parse"], make_metadata())
    scheduler.schedule("job-1", "parse")
    scheduler.reserve("job-1")
    registry.set_status("worker-1", "offline")
    registry.register("worker-2", ["parse"], make_metadata())

    scheduler.rebalance()
    stats = scheduler.stats()

    assert stats["scheduled"] == 1
    assert stats["rebalanced"] == 1


def test_api_schedule(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.post("/cluster/schedule", json={"job_id": "job-1", "capability": "parse"})

    assert response.status_code == 200
    assert response.json()["decision"]["worker_id"] == "worker-1"


def test_api_schedule_missing_field_returns_422(client: TestClient):
    response = client.post("/cluster/schedule", json={"capability": "parse"})

    assert response.status_code == 422


def test_api_get_schedule(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    client.post("/cluster/schedule", json={"job_id": "job-1", "capability": "parse"})

    response = client.get("/cluster/schedule/job-1")

    assert response.status_code == 200
    assert response.json()["job_id"] == "job-1"


def test_api_get_schedule_not_found(client: TestClient):
    response = client.get("/cluster/schedule/does-not-exist")

    assert response.status_code == 404


def test_api_rebalance(client: TestClient, registry: WorkerRegistry, scheduler: DistributedScheduler):
    registry.register("worker-1", ["parse"], make_metadata())
    client.post("/cluster/schedule", json={"job_id": "job-1", "capability": "parse"})
    scheduler.reserve("job-1")
    registry.set_status("worker-1", "offline")
    registry.register("worker-2", ["parse"], make_metadata())

    response = client.post("/cluster/rebalance")

    assert response.status_code == 200
    assert response.json()[0]["decision"]["worker_id"] == "worker-2"


def test_api_scheduler_stats(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    client.post("/cluster/schedule", json={"job_id": "job-1", "capability": "parse"})

    response = client.get("/cluster/scheduler/stats")

    assert response.status_code == 200
    assert response.json()["scheduled"] == 1
