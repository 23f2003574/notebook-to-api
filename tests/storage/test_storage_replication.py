import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.object_storage import ObjectStorageEngine
from backend.storage.storage_replication import (
    JobStatus,
    ReplicationJob,
    ReplicationMode,
    ReplicationStatus,
    StorageReplicationEngine,
    get_storage_replication_engine,
    router as storage_replication_router,
)


@pytest.fixture
def primary() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def replica_a() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def replica_b() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def engine(primary: ObjectStorageEngine, replica_a: ObjectStorageEngine, replica_b: ObjectStorageEngine) -> StorageReplicationEngine:
    return StorageReplicationEngine(primary=primary, replicas={"replica-a": replica_a, "replica-b": replica_b})


@pytest.fixture
def client(engine: StorageReplicationEngine) -> TestClient:
    app = FastAPI()
    app.include_router(storage_replication_router)
    app.dependency_overrides[get_storage_replication_engine] = lambda: engine
    return TestClient(app)


def test_replicate_copies_to_all_replicas(
    engine: StorageReplicationEngine, primary: ObjectStorageEngine, replica_a: ObjectStorageEngine, replica_b: ObjectStorageEngine
):
    primary.put("a.txt", b"hello")

    job = engine.replicate("a.txt")

    assert isinstance(job, ReplicationJob)
    assert job.status == JobStatus.COMPLETED
    assert replica_a.get("a.txt").data == b"hello"
    assert replica_b.get("a.txt").data == b"hello"


def test_replicate_raises_for_missing_key(engine: StorageReplicationEngine):
    with pytest.raises(KeyError):
        engine.replicate("missing")


def test_replicate_raises_without_replicas(primary: ObjectStorageEngine):
    primary.put("a.txt", b"hello")
    engine = StorageReplicationEngine(primary=primary)

    with pytest.raises(ValueError):
        engine.replicate("a.txt")


def test_replicate_targets_specific_replicas(
    engine: StorageReplicationEngine, primary: ObjectStorageEngine, replica_a: ObjectStorageEngine, replica_b: ObjectStorageEngine
):
    primary.put("a.txt", b"hello")

    engine.replicate("a.txt", ["replica-a"])

    assert replica_a.exists("a.txt") is True
    assert replica_b.exists("a.txt") is False


def test_replicate_incremental_skips_already_synced_replica(
    engine: StorageReplicationEngine, primary: ObjectStorageEngine, replica_a: ObjectStorageEngine
):
    primary.put("a.txt", b"hello")
    engine.replicate("a.txt", ["replica-a"], mode=ReplicationMode.FULL)
    replica_a.put("a.txt", b"hello", content_type="text/plain")

    job = engine.replicate("a.txt", ["replica-a"], mode=ReplicationMode.INCREMENTAL)

    assert job.status == JobStatus.COMPLETED
    assert job.results["replica-a"] is True


def test_sync_replicates_all_primary_keys(
    engine: StorageReplicationEngine, primary: ObjectStorageEngine, replica_a: ObjectStorageEngine
):
    primary.put("a.txt", b"one")
    primary.put("b.txt", b"two")

    jobs = engine.sync()

    assert {job.key for job in jobs} == {"a.txt", "b.txt"}
    assert replica_a.get("a.txt").data == b"one"
    assert replica_a.get("b.txt").data == b"two"


def test_sync_specific_key(engine: StorageReplicationEngine, primary: ObjectStorageEngine, replica_a: ObjectStorageEngine):
    primary.put("a.txt", b"one")
    primary.put("b.txt", b"two")

    jobs = engine.sync("a.txt")

    assert [job.key for job in jobs] == ["a.txt"]
    assert replica_a.exists("b.txt") is False


def test_verify_returns_in_sync_when_checksums_match(
    engine: StorageReplicationEngine, primary: ObjectStorageEngine
):
    primary.put("a.txt", b"hello")
    engine.replicate("a.txt", ["replica-a"])

    status = engine.verify("a.txt", "replica-a")

    assert isinstance(status, ReplicationStatus)
    assert status.in_sync is True


def test_verify_returns_out_of_sync_when_replica_missing_object(
    engine: StorageReplicationEngine, primary: ObjectStorageEngine
):
    primary.put("a.txt", b"hello")

    status = engine.verify("a.txt", "replica-a")

    assert status.in_sync is False
    assert status.checksum is None


def test_verify_detects_drifted_replica(
    engine: StorageReplicationEngine, primary: ObjectStorageEngine, replica_a: ObjectStorageEngine
):
    primary.put("a.txt", b"hello")
    engine.replicate("a.txt", ["replica-a"])
    replica_a.put("a.txt", b"drifted")

    status = engine.verify("a.txt", "replica-a")

    assert status.in_sync is False


def test_verify_raises_for_missing_key(engine: StorageReplicationEngine):
    with pytest.raises(KeyError):
        engine.verify("missing", "replica-a")


def test_verify_raises_for_unknown_replica(engine: StorageReplicationEngine, primary: ObjectStorageEngine):
    primary.put("a.txt", b"hello")

    with pytest.raises(KeyError):
        engine.verify("a.txt", "unknown-replica")


def test_repair_fixes_drifted_replica(
    engine: StorageReplicationEngine, primary: ObjectStorageEngine, replica_a: ObjectStorageEngine
):
    primary.put("a.txt", b"hello")
    engine.replicate("a.txt", ["replica-a"])
    replica_a.put("a.txt", b"drifted")

    jobs = engine.repair("a.txt", replica_id="replica-a")

    assert len(jobs) == 1
    assert replica_a.get("a.txt").data == b"hello"


def test_repair_skips_replicas_already_in_sync(
    engine: StorageReplicationEngine, primary: ObjectStorageEngine
):
    primary.put("a.txt", b"hello")
    engine.replicate("a.txt")

    jobs = engine.repair("a.txt")

    assert jobs == []


def test_list_status_covers_all_keys_and_replicas(
    engine: StorageReplicationEngine, primary: ObjectStorageEngine
):
    primary.put("a.txt", b"one")
    primary.put("b.txt", b"two")
    engine.replicate("a.txt")

    statuses = engine.list_status()

    assert len(statuses) == 4


def test_get_job_returns_recorded_job(engine: StorageReplicationEngine, primary: ObjectStorageEngine):
    primary.put("a.txt", b"hello")
    job = engine.replicate("a.txt")

    fetched = engine.get_job(job.job_id)

    assert fetched is job


def test_get_job_returns_none_for_missing_job(engine: StorageReplicationEngine):
    assert engine.get_job("missing") is None


def test_api_replicate_and_get_job(client: TestClient, primary: ObjectStorageEngine):
    primary.put("a.txt", b"hello")

    replicate_response = client.post("/storage/replication", params={"key": "a.txt"})
    assert replicate_response.status_code == 200
    job_id = replicate_response.json()["job_id"]

    get_response = client.get(f"/storage/replication/{job_id}")
    assert get_response.status_code == 200
    assert get_response.json()["job_id"] == job_id


def test_api_replicate_missing_key_returns_404(client: TestClient):
    response = client.post("/storage/replication", params={"key": "missing"})
    assert response.status_code == 404


def test_api_sync_endpoint(client: TestClient, primary: ObjectStorageEngine):
    primary.put("a.txt", b"hello")

    response = client.post("/storage/replication/sync")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_status_endpoint_not_shadowed_by_job_route(client: TestClient, primary: ObjectStorageEngine):
    primary.put("a.txt", b"hello")
    client.post("/storage/replication", params={"key": "a.txt"})

    response = client.get("/storage/replication/status")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_api_get_job_missing_returns_404(client: TestClient):
    response = client.get("/storage/replication/does-not-exist")
    assert response.status_code == 404
