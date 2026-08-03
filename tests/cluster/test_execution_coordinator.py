import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.execution_coordinator import (
    ASSIGNED,
    CANCELLED,
    COMPLETED,
    ExecutionCoordinator,
    ExecutionSession,
    FAILED,
    QUEUED,
    RUNNING,
    get_execution_coordinator,
    router as execution_coordinator_router,
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
    return WorkerDiscoveryService(registry, stale_after_seconds=30.0)


@pytest.fixture
def dispatcher(discovery: WorkerDiscoveryService) -> DistributedJobDispatcher:
    return DistributedJobDispatcher(discovery)


@pytest.fixture
def coordinator(dispatcher: DistributedJobDispatcher) -> ExecutionCoordinator:
    return ExecutionCoordinator(dispatcher)


@pytest.fixture
def client(coordinator: ExecutionCoordinator) -> TestClient:
    app = FastAPI()
    app.include_router(execution_coordinator_router)
    app.dependency_overrides[get_execution_coordinator] = lambda: coordinator
    return TestClient(app)


def test_submit_assigns_when_worker_available(registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())

    session = coordinator.submit("job-1", "parse")

    assert isinstance(session, ExecutionSession)
    assert session.state == ASSIGNED
    assert session.worker_id == "worker-1"
    assert session.progress == pytest.approx(0.25)


def test_submit_queues_when_no_worker_available(coordinator: ExecutionCoordinator):
    session = coordinator.submit("job-1", "parse")

    assert session.state == QUEUED
    assert session.worker_id is None
    assert session.progress == 0.0


def test_submit_rejects_duplicate_active_execution(registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    with pytest.raises(ValueError):
        coordinator.submit("job-1", "parse")


def test_submit_allows_resubmission_after_terminal_state(
    registry: WorkerRegistry, coordinator: ExecutionCoordinator
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1")

    session = coordinator.submit("job-1", "parse")

    assert session.state == ASSIGNED


def test_monitor_transitions_assigned_to_running(registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    session = coordinator.monitor("job-1")

    assert session.state == RUNNING
    assert session.progress == pytest.approx(0.6)


def test_monitor_transitions_queued_to_assigned_once_worker_appears(
    registry: WorkerRegistry, dispatcher: DistributedJobDispatcher, coordinator: ExecutionCoordinator
):
    coordinator.submit("job-1", "parse")
    registry.register("worker-1", ["parse"], make_metadata())
    dispatcher.reassign("job-1")

    session = coordinator.monitor("job-1")

    assert session.state == ASSIGNED
    assert session.worker_id == "worker-1"


def test_monitor_missing_execution_raises(coordinator: ExecutionCoordinator):
    with pytest.raises(KeyError):
        coordinator.monitor("does-not-exist")


def test_monitor_is_a_no_op_once_terminal(registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1")

    session = coordinator.monitor("job-1")

    assert session.state == COMPLETED


def test_complete_success_marks_completed_and_releases_worker(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, coordinator: ExecutionCoordinator
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    session = coordinator.complete("job-1", result={"rows": 10})

    assert session.state == COMPLETED
    assert session.result == {"rows": 10}
    assert session.progress == 1.0
    assert discovery.get_load("worker-1") == 0


def test_complete_failure_marks_failed_with_error(registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    session = coordinator.complete("job-1", success=False, error="boom")

    assert session.state == FAILED
    assert session.error == "boom"


def test_complete_missing_execution_raises(coordinator: ExecutionCoordinator):
    with pytest.raises(KeyError):
        coordinator.complete("does-not-exist")


def test_complete_already_terminal_raises(registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1")

    with pytest.raises(ValueError):
        coordinator.complete("job-1")


def test_cancel_queued_execution(coordinator: ExecutionCoordinator):
    coordinator.submit("job-1", "parse")

    session = coordinator.cancel("job-1")

    assert session.state == CANCELLED


def test_cancel_releases_worker_load(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, coordinator: ExecutionCoordinator
):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")

    coordinator.cancel("job-1")

    assert discovery.get_load("worker-1") == 0


def test_cancel_missing_execution_raises(coordinator: ExecutionCoordinator):
    with pytest.raises(KeyError):
        coordinator.cancel("does-not-exist")


def test_cancel_already_terminal_raises(registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.cancel("job-1")

    with pytest.raises(ValueError):
        coordinator.cancel("job-1")


def test_list_executions_returns_sorted_sessions(registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-b", "parse")
    coordinator.submit("job-a", "parse")

    sessions = coordinator.list_executions()

    assert [session.execution_id for session in sessions] == ["job-a", "job-b"]


def test_list_executions_filters_by_state(registry: WorkerRegistry, coordinator: ExecutionCoordinator):
    registry.register("worker-1", ["parse"], make_metadata())
    coordinator.submit("job-1", "parse")
    coordinator.submit("job-2", "export")

    assigned = coordinator.list_executions(state=ASSIGNED)
    queued = coordinator.list_executions(state=QUEUED)

    assert [session.execution_id for session in assigned] == ["job-1"]
    assert [session.execution_id for session in queued] == ["job-2"]


def test_api_submit(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.post("/cluster/executions", json={"job_id": "job-1", "capability": "parse"})

    assert response.status_code == 200
    assert response.json()["state"] == ASSIGNED


def test_api_submit_missing_field_returns_422(client: TestClient):
    response = client.post("/cluster/executions", json={"capability": "parse"})

    assert response.status_code == 422


def test_api_monitor(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    client.post("/cluster/executions", json={"job_id": "job-1", "capability": "parse"})

    response = client.get("/cluster/executions/job-1")

    assert response.status_code == 200
    assert response.json()["state"] == RUNNING


def test_api_monitor_not_found(client: TestClient):
    response = client.get("/cluster/executions/does-not-exist")

    assert response.status_code == 404


def test_api_cancel(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    client.post("/cluster/executions", json={"job_id": "job-1", "capability": "parse"})

    response = client.delete("/cluster/executions/job-1")

    assert response.status_code == 200
    assert response.json()["state"] == CANCELLED


def test_api_cancel_not_found(client: TestClient):
    response = client.delete("/cluster/executions/does-not-exist")

    assert response.status_code == 404


def test_api_cancel_already_terminal_returns_409(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    client.post("/cluster/executions", json={"job_id": "job-1", "capability": "parse"})
    client.delete("/cluster/executions/job-1")

    response = client.delete("/cluster/executions/job-1")

    assert response.status_code == 409


def test_api_list_executions(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    client.post("/cluster/executions", json={"job_id": "job-1", "capability": "parse"})

    response = client.get("/cluster/executions")

    assert response.status_code == 200
    assert len(response.json()) == 1
