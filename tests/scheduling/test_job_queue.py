import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.scheduling.job_queue import (
    EmptyQueueError,
    JobQueueManager,
    QueuedJob,
    QueueState,
    get_job_queue_manager,
    router as job_queue_router,
)
from backend.scheduling.job_registry import (
    JobRegistry,
    JobStatus,
    UnknownJobError,
    get_job_registry,
)


@pytest.fixture
def queue() -> JobQueueManager:
    return JobQueueManager()


@pytest.fixture
def registry() -> JobRegistry:
    return JobRegistry()


@pytest.fixture
def client(queue: JobQueueManager, registry: JobRegistry) -> TestClient:
    app = FastAPI()
    app.include_router(job_queue_router)
    app.dependency_overrides[get_job_queue_manager] = lambda: queue
    app.dependency_overrides[get_job_registry] = lambda: registry
    return TestClient(app)


def test_enqueue_creates_queued_job(queue: JobQueueManager):
    queued = queue.enqueue("nightly-export")

    assert isinstance(queued, QueuedJob)
    assert queued.job_id == "nightly-export"
    assert queued.priority == 0


def test_enqueue_rejects_empty_job_id(queue: JobQueueManager):
    with pytest.raises(ValueError):
        queue.enqueue("")


def test_enqueue_rejects_negative_delay(queue: JobQueueManager):
    with pytest.raises(ValueError):
        queue.enqueue("nightly-export", delay_seconds=-1)


def test_enqueue_validates_against_registry(queue: JobQueueManager, registry: JobRegistry):
    with pytest.raises(UnknownJobError):
        queue.enqueue("does-not-exist", registry=registry)

    registry.register("nightly-export", "batch")
    queued = queue.enqueue("nightly-export", registry=registry)
    assert queued.job_id == "nightly-export"


def test_dequeue_returns_fifo_order(queue: JobQueueManager):
    queue.enqueue("first")
    queue.enqueue("second")

    assert queue.dequeue().job_id == "first"
    assert queue.dequeue().job_id == "second"


def test_dequeue_respects_priority_order(queue: JobQueueManager):
    queue.enqueue("low", priority=0)
    queue.enqueue("high", priority=10)

    assert queue.dequeue().job_id == "high"
    assert queue.dequeue().job_id == "low"


def test_dequeue_updates_registry_status(queue: JobQueueManager, registry: JobRegistry):
    registry.register("nightly-export", "batch")
    queue.enqueue("nightly-export", registry=registry)

    queue.dequeue(registry=registry)

    assert registry.get("nightly-export").status == JobStatus.RUNNING


def test_dequeue_empty_queue_raises(queue: JobQueueManager):
    with pytest.raises(EmptyQueueError):
        queue.dequeue()


def test_dequeue_skips_delayed_jobs(queue: JobQueueManager):
    queue.enqueue("delayed", delay_seconds=60)

    with pytest.raises(EmptyQueueError):
        queue.dequeue()


def test_dequeue_picks_up_delayed_job_once_ready(queue: JobQueueManager):
    queue.enqueue("delayed", delay_seconds=0.01)
    time.sleep(0.02)

    assert queue.dequeue().job_id == "delayed"


def test_peek_does_not_remove_job(queue: JobQueueManager):
    queue.enqueue("nightly-export")

    peeked = queue.peek()

    assert peeked.job_id == "nightly-export"
    assert queue.peek().job_id == "nightly-export"


def test_peek_empty_queue_raises(queue: JobQueueManager):
    with pytest.raises(EmptyQueueError):
        queue.peek()


def test_clear_removes_all_jobs(queue: JobQueueManager):
    queue.enqueue("first")
    queue.enqueue("second")

    removed = queue.clear()

    assert removed == 2
    with pytest.raises(EmptyQueueError):
        queue.peek()


def test_stats_reports_ready_and_delayed(queue: JobQueueManager):
    queue.enqueue("ready-job")
    queue.enqueue("delayed-job", delay_seconds=60)

    state = queue.stats()

    assert isinstance(state, QueueState)
    assert state.size == 2
    assert state.ready == 1
    assert state.delayed == 1


def test_api_enqueue_and_list(client: TestClient, registry: JobRegistry):
    registry.register("nightly-export", "batch")

    response = client.post("/jobs/queue", json={"job_id": "nightly-export"})
    assert response.status_code == 201
    assert response.json()["job_id"] == "nightly-export"

    listed = client.get("/jobs/queue")
    assert listed.status_code == 200
    body = listed.json()
    assert len(body["jobs"]) == 1
    assert body["state"]["size"] == 1


def test_api_enqueue_unknown_job_returns_404(client: TestClient):
    response = client.post("/jobs/queue", json={"job_id": "does-not-exist"})

    assert response.status_code == 404


def test_api_peek_next(client: TestClient, registry: JobRegistry):
    registry.register("nightly-export", "batch")
    client.post("/jobs/queue", json={"job_id": "nightly-export"})

    response = client.get("/jobs/queue/next")

    assert response.status_code == 200
    assert response.json()["job_id"] == "nightly-export"


def test_api_peek_next_empty_returns_404(client: TestClient):
    response = client.get("/jobs/queue/next")

    assert response.status_code == 404


def test_api_clear_queue(client: TestClient, registry: JobRegistry):
    registry.register("nightly-export", "batch")
    client.post("/jobs/queue", json={"job_id": "nightly-export"})

    response = client.delete("/jobs/queue")
    assert response.status_code == 204

    listed = client.get("/jobs/queue")
    assert listed.json()["state"]["size"] == 0
