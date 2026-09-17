import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.execution_coordinator import ExecutionCoordinator
from backend.cluster.fault_tolerance import (
    FailureEvent,
    FaultToleranceManager,
    RecoveryPlan,
    get_fault_tolerance_manager,
    router as fault_tolerance_router,
)
from backend.cluster.job_dispatcher import DistributedJobDispatcher
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
def dispatcher(discovery: WorkerDiscoveryService) -> DistributedJobDispatcher:
    return DistributedJobDispatcher(discovery)


@pytest.fixture
def coordinator(dispatcher: DistributedJobDispatcher) -> ExecutionCoordinator:
    return ExecutionCoordinator(dispatcher)


@pytest.fixture
def manager(
    coordinator: ExecutionCoordinator,
    dispatcher: DistributedJobDispatcher,
    registry: WorkerRegistry,
    discovery: WorkerDiscoveryService,
) -> FaultToleranceManager:
    return FaultToleranceManager(coordinator, dispatcher, registry, discovery)


@pytest.fixture
def client(manager: FaultToleranceManager) -> TestClient:
    app = FastAPI()
    app.include_router(fault_tolerance_router)
    app.dependency_overrides[get_fault_tolerance_manager] = lambda: manager
    return TestClient(app)


def test_detect_failure_classifies_execution_failed(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1", success=False, error="boom")

    event = manager.detect_failure("job-1")

    assert isinstance(event, FailureEvent)
    assert event.failure_type == "execution_failed"


def test_detect_failure_classifies_worker_offline(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    registry.set_status("worker-1", "offline")

    event = manager.detect_failure("job-1")

    assert event.failure_type == "worker_offline"


def test_detect_failure_classifies_worker_unhealthy(
    registry: WorkerRegistry,
    discovery: WorkerDiscoveryService,
    coordinator: ExecutionCoordinator,
    manager: FaultToleranceManager,
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    discovery.set_health("worker-1", "unhealthy")

    event = manager.detect_failure("job-1")

    assert event.failure_type == "worker_unhealthy"


def test_detect_failure_accepts_explicit_failure_type(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    event = manager.detect_failure("job-1", failure_type="timeout", detail="exceeded 30s")

    assert event.failure_type == "timeout"
    assert event.detail == "exceeded 30s"


def test_detect_failure_missing_execution_raises(manager: FaultToleranceManager):
    with pytest.raises(KeyError):
        manager.detect_failure("does-not-exist")


def test_retry_resubmits_failed_execution(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse", payload={"rows": 10})
    coordinator.complete("job-1", success=False, error="boom")

    plan = manager.retry("job-1")

    assert isinstance(plan, RecoveryPlan)
    assert plan.strategy == "retry"
    assert plan.success is True
    assert coordinator.get_session("job-1").attempt == 2


def test_retry_preserves_original_payload_and_policy(
    registry: WorkerRegistry,
    dispatcher: DistributedJobDispatcher,
    coordinator: ExecutionCoordinator,
    manager: FaultToleranceManager,
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse", policy="capability_match", payload={"rows": 42})
    coordinator.complete("job-1", success=False, error="boom")

    manager.retry("job-1")

    task = dispatcher.get_serialized_task("job-1")
    assert task.payload == {"rows": 42}


def test_retry_non_failed_execution_raises(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    with pytest.raises(ValueError):
        manager.retry("job-1")


def test_retry_missing_execution_raises(manager: FaultToleranceManager):
    with pytest.raises(KeyError):
        manager.retry("does-not-exist")


def test_reassign_moves_job_to_new_worker(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    registry.register("worker-2", ["parse"], make_metadata())
    registry.set_status("worker-1", "draining")

    plan = manager.reassign("job-1")

    assert plan.strategy == "task_reassignment"
    assert plan.new_worker_id == "worker-2"
    assert plan.success is True


def test_reassign_reports_failure_when_no_worker_available(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    registry.set_status("worker-1", "draining")

    plan = manager.reassign("job-1")

    assert plan.new_worker_id is None
    assert plan.success is False


def test_recover_routes_worker_offline_to_failover(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    registry.register("worker-2", ["parse"], make_metadata())
    registry.set_status("worker-1", "offline")

    plan = manager.recover("job-1")

    assert plan.strategy == "worker_failover"
    assert plan.new_worker_id == "worker-2"


def test_recover_routes_execution_failed_to_retry(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1", success=False, error="boom")

    plan = manager.recover("job-1")

    assert plan.strategy == "retry"


def test_recover_with_checkpoint_uses_checkpoint_restore(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1", success=False, error="boom")

    plan = manager.recover("job-1", checkpoint={"progress": 0.6})

    assert plan.strategy == "checkpoint_restore"
    assert plan.success is True


def test_list_events_filters_by_execution_id(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.submit("job-2", "parse")
    manager.detect_failure("job-1", failure_type="timeout")
    manager.detect_failure("job-2", failure_type="timeout")

    events = manager.list_events(execution_id="job-1")

    assert len(events) == 1
    assert events[0].execution_id == "job-1"


def test_api_recover(client: TestClient, registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1", success=False, error="boom")

    response = client.post("/cluster/recovery", json={"execution_id": "job-1"})

    assert response.status_code == 200
    assert response.json()["strategy"] == "retry"


def test_api_recover_missing_execution_returns_404(client: TestClient):
    response = client.post("/cluster/recovery", json={"execution_id": "does-not-exist"})

    assert response.status_code == 404


def test_api_retry(client: TestClient, registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1", success=False, error="boom")

    response = client.post("/cluster/recovery/retry", json={"execution_id": "job-1"})

    assert response.status_code == 200
    assert response.json()["strategy"] == "retry"


def test_api_retry_not_failed_returns_409(client: TestClient, registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    response = client.post("/cluster/recovery/retry", json={"execution_id": "job-1"})

    assert response.status_code == 409


def test_api_reassign(client: TestClient, registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    registry.register("worker-2", ["parse"], make_metadata())
    registry.set_status("worker-1", "draining")

    response = client.post("/cluster/recovery/reassign", json={"execution_id": "job-1"})

    assert response.status_code == 200
    assert response.json()["new_worker_id"] == "worker-2"


def test_api_reassign_missing_execution_returns_404(client: TestClient):
    response = client.post("/cluster/recovery/reassign", json={"execution_id": "does-not-exist"})

    assert response.status_code == 404


def test_api_events(client: TestClient, registry: WorkerRegistry, coordinator: ExecutionCoordinator, manager: FaultToleranceManager):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    manager.detect_failure("job-1", failure_type="timeout")

    response = client.get("/cluster/recovery/events")

    assert response.status_code == 200
    assert len(response.json()) == 1
