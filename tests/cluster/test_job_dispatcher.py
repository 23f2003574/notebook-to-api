import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.job_dispatcher import (
    DispatchRequest,
    DispatchResult,
    DistributedJobDispatcher,
    get_job_dispatcher,
    router as job_dispatcher_router,
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
    return WorkerDiscoveryService(registry, stale_after_seconds=30.0)


@pytest.fixture
def dispatcher(discovery: WorkerDiscoveryService) -> DistributedJobDispatcher:
    return DistributedJobDispatcher(discovery)


@pytest.fixture
def client(dispatcher: DistributedJobDispatcher) -> TestClient:
    app = FastAPI()
    app.include_router(job_dispatcher_router)
    app.dependency_overrides[get_job_dispatcher] = lambda: dispatcher
    return TestClient(app)


def test_select_worker_capability_match_picks_lowest_id(
    registry: WorkerRegistry, dispatcher: DistributedJobDispatcher
):
    registry.register("worker-b", ["parse"], make_metadata())
    registry.register("worker-a", ["parse"], make_metadata())

    worker = dispatcher.select_worker(DispatchRequest("job-1", "parse", policy="capability_match"))

    assert worker.worker_id == "worker-a"


def test_select_worker_ignores_workers_without_capability(
    registry: WorkerRegistry, dispatcher: DistributedJobDispatcher
):
    registry.register("worker-1", ["export"], make_metadata())

    worker = dispatcher.select_worker(DispatchRequest("job-1", "parse", policy="capability_match"))

    assert worker is None


def test_select_worker_least_loaded_prefers_idle_worker(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, dispatcher: DistributedJobDispatcher
):
    registry.register("worker-busy", ["parse"], make_metadata())
    registry.register("worker-idle", ["parse"], make_metadata())
    discovery.set_load("worker-busy", 5)

    worker = dispatcher.select_worker(DispatchRequest("job-1", "parse", policy="least_loaded"))

    assert worker.worker_id == "worker-idle"


def test_select_worker_round_robin_rotates(registry: WorkerRegistry, dispatcher: DistributedJobDispatcher):
    registry.register("worker-a", ["parse"], make_metadata())
    registry.register("worker-b", ["parse"], make_metadata())

    request = DispatchRequest("job-1", "parse", policy="round_robin")
    first = dispatcher.select_worker(request)
    second = dispatcher.select_worker(request)
    third = dispatcher.select_worker(request)

    assert [first.worker_id, second.worker_id, third.worker_id] == ["worker-a", "worker-b", "worker-a"]


def test_select_worker_rejects_unknown_policy(registry: WorkerRegistry, dispatcher: DistributedJobDispatcher):
    registry.register("worker-1", ["parse"], make_metadata())

    with pytest.raises(ValueError):
        dispatcher.select_worker(DispatchRequest("job-1", "parse", policy="magic"))


def test_dispatch_assigns_available_worker(registry: WorkerRegistry, dispatcher: DistributedJobDispatcher):
    registry.register("worker-1", ["parse"], make_metadata())

    result = dispatcher.dispatch(DispatchRequest("job-1", "parse"))

    assert isinstance(result, DispatchResult)
    assert result.worker_id == "worker-1"
    assert result.status == "dispatched"


def test_dispatch_increments_worker_load(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, dispatcher: DistributedJobDispatcher
):
    registry.register("worker-1", ["parse"], make_metadata())

    dispatcher.dispatch(DispatchRequest("job-1", "parse"))

    assert discovery.get_load("worker-1") == 1


def test_dispatch_queues_when_no_worker_available(dispatcher: DistributedJobDispatcher):
    result = dispatcher.dispatch(DispatchRequest("job-1", "parse"))

    assert result.status == "queued"
    assert result.worker_id is None
    assert dispatcher.queue_status()[0]["job_id"] == "job-1"


def test_dispatch_rejects_empty_job_id(dispatcher: DistributedJobDispatcher):
    with pytest.raises(ValueError):
        dispatcher.dispatch(DispatchRequest("", "parse"))


def test_get_dispatch_returns_latest_result(registry: WorkerRegistry, dispatcher: DistributedJobDispatcher):
    registry.register("worker-1", ["parse"], make_metadata())
    dispatcher.dispatch(DispatchRequest("job-1", "parse"))

    result = dispatcher.get_dispatch("job-1")

    assert result.job_id == "job-1"


def test_get_dispatch_missing_job_returns_none(dispatcher: DistributedJobDispatcher):
    assert dispatcher.get_dispatch("does-not-exist") is None


def test_reassign_moves_job_to_new_worker(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, dispatcher: DistributedJobDispatcher
):
    registry.register("worker-1", ["parse"], make_metadata())
    dispatcher.dispatch(DispatchRequest("job-1", "parse", policy="capability_match"))
    registry.register("worker-2", ["parse"], make_metadata())
    registry.set_status("worker-1", "draining")

    result = dispatcher.reassign("job-1")

    assert result.worker_id == "worker-2"
    assert discovery.get_load("worker-1") == 0
    assert discovery.get_load("worker-2") == 1


def test_reassign_queues_when_no_replacement_available(
    registry: WorkerRegistry, dispatcher: DistributedJobDispatcher
):
    registry.register("worker-1", ["parse"], make_metadata())
    dispatcher.dispatch(DispatchRequest("job-1", "parse"))
    registry.set_status("worker-1", "draining")

    result = dispatcher.reassign("job-1")

    assert result.status == "queued"


def test_reassign_missing_job_raises(dispatcher: DistributedJobDispatcher):
    with pytest.raises(KeyError):
        dispatcher.reassign("does-not-exist")


def test_queue_status_orders_by_priority(dispatcher: DistributedJobDispatcher):
    dispatcher.dispatch(DispatchRequest("job-low", "parse", priority=1))
    dispatcher.dispatch(DispatchRequest("job-high", "parse", priority=10))

    statuses = dispatcher.queue_status()

    assert [status["job_id"] for status in statuses] == ["job-high", "job-low"]


def test_queue_status_empty_when_nothing_queued(dispatcher: DistributedJobDispatcher):
    assert dispatcher.queue_status() == []


def test_api_dispatch(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.post("/cluster/dispatch", json={"job_id": "job-1", "capability": "parse"})

    assert response.status_code == 200
    assert response.json()["worker_id"] == "worker-1"


def test_api_dispatch_missing_field_returns_422(client: TestClient):
    response = client.post("/cluster/dispatch", json={"capability": "parse"})

    assert response.status_code == 422


def test_api_get_dispatch(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    client.post("/cluster/dispatch", json={"job_id": "job-1", "capability": "parse"})

    response = client.get("/cluster/dispatch/job-1")

    assert response.status_code == 200
    assert response.json()["job_id"] == "job-1"


def test_api_get_dispatch_not_found(client: TestClient):
    response = client.get("/cluster/dispatch/does-not-exist")

    assert response.status_code == 404


def test_api_queue_status(client: TestClient):
    client.post("/cluster/dispatch", json={"job_id": "job-1", "capability": "parse"})

    response = client.get("/cluster/dispatch/queue")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_reassign(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())
    client.post("/cluster/dispatch", json={"job_id": "job-1", "capability": "parse"})
    registry.register("worker-2", ["parse"], make_metadata())
    registry.set_status("worker-1", "draining")

    response = client.post("/cluster/dispatch/reassign", json={"job_id": "job-1"})

    assert response.status_code == 200
    assert response.json()["worker_id"] == "worker-2"


def test_api_reassign_missing_job_returns_404(client: TestClient):
    response = client.post("/cluster/dispatch/reassign", json={"job_id": "does-not-exist"})

    assert response.status_code == 404
