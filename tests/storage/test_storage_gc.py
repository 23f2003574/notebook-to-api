import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.artifact_manager import ArtifactManager, ArtifactType
from backend.storage.object_storage import ObjectStorageEngine
from backend.storage.storage_gc import (
    CleanupReport,
    CleanupTarget,
    GarbageCandidate,
    StorageGarbageCollector,
    get_storage_garbage_collector,
    router as storage_gc_router,
)
from backend.storage.storage_versioning import StorageVersionManager


@pytest.fixture
def object_storage() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def artifact_manager(object_storage: ObjectStorageEngine) -> ArtifactManager:
    return ArtifactManager(object_storage=object_storage)


@pytest.fixture
def version_manager(object_storage: ObjectStorageEngine, artifact_manager: ArtifactManager) -> StorageVersionManager:
    return StorageVersionManager(object_storage=object_storage, artifact_manager=artifact_manager)


@pytest.fixture
def gc(
    object_storage: ObjectStorageEngine, artifact_manager: ArtifactManager, version_manager: StorageVersionManager
) -> StorageGarbageCollector:
    return StorageGarbageCollector(
        object_storage=object_storage, artifact_manager=artifact_manager, version_manager=version_manager
    )


@pytest.fixture
def client(gc: StorageGarbageCollector) -> TestClient:
    app = FastAPI()
    app.include_router(storage_gc_router)
    app.dependency_overrides[get_storage_garbage_collector] = lambda: gc
    return TestClient(app)


def test_scan_finds_orphaned_object(gc: StorageGarbageCollector, object_storage: ObjectStorageEngine):
    object_storage.put("orphan.bin", b"data")

    candidates = gc.scan()

    assert len(candidates) == 1
    assert isinstance(candidates[0], GarbageCandidate)
    assert candidates[0].target == CleanupTarget.ORPHANED_OBJECT
    assert candidates[0].key == "orphan.bin"


def test_scan_does_not_flag_live_artifact_object(gc: StorageGarbageCollector, artifact_manager: ArtifactManager):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    candidates = gc.scan()

    assert candidates == []


def test_scan_flags_superseded_version(
    gc: StorageGarbageCollector, artifact_manager: ArtifactManager, version_manager: StorageVersionManager
):
    artifact = artifact_manager.create("model.pkl", ArtifactType.MODEL, b"v1")
    version_manager.create_version(artifact.artifact_id, b"v1")
    version_manager.create_version(artifact.artifact_id, b"v2")

    # artifact_manager.create() seeds its own object; once versioning takes over and
    # repoints the artifact at v2, both that seed object and superseded v1 are reclaimable.
    candidates = gc.scan()
    targets = {candidate.target for candidate in candidates}

    assert len(candidates) == 2
    assert CleanupTarget.EXPIRED_VERSION in targets
    assert CleanupTarget.ORPHANED_OBJECT in targets


def test_scan_flags_failed_artifact(gc: StorageGarbageCollector, artifact_manager: ArtifactManager):
    artifact = artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")
    artifact_manager.mark_failed(artifact.artifact_id)

    candidates = gc.scan()

    assert len(candidates) == 1
    assert candidates[0].target == CleanupTarget.FAILED_ARTIFACT
    assert candidates[0].key == artifact.object_key


def test_scan_uses_custom_candidate_provider(gc: StorageGarbageCollector):
    from datetime import datetime, timezone

    custom = GarbageCandidate(
        candidate_id="custom-1",
        key="uploads/tmp-123",
        target=CleanupTarget.TEMPORARY_UPLOAD,
        size=100,
        reason="stale upload session",
        discovered_at=datetime.now(timezone.utc),
    )
    gc.add_candidate_provider(lambda: [custom])

    candidates = gc.scan()

    assert custom in candidates


def test_mark_requires_prior_scan(gc: StorageGarbageCollector):
    with pytest.raises(ValueError):
        gc.mark()


def test_mark_all_candidates_by_default(gc: StorageGarbageCollector, object_storage: ObjectStorageEngine):
    object_storage.put("orphan.bin", b"data")
    gc.scan()

    marked = gc.mark()

    assert len(marked) == 1


def test_mark_specific_candidate_ids(gc: StorageGarbageCollector, object_storage: ObjectStorageEngine):
    object_storage.put("a.bin", b"1")
    object_storage.put("b.bin", b"2")
    candidates = gc.scan()
    target_id = next(c.candidate_id for c in candidates if c.key == "a.bin")

    marked = gc.mark([target_id])

    assert len(marked) == 1
    assert marked[0].key == "a.bin"


def test_mark_rejects_unknown_candidate_id(gc: StorageGarbageCollector, object_storage: ObjectStorageEngine):
    object_storage.put("a.bin", b"1")
    gc.scan()

    with pytest.raises(ValueError):
        gc.mark(["does-not-exist"])


def test_sweep_requires_prior_mark(gc: StorageGarbageCollector):
    with pytest.raises(ValueError):
        gc.sweep()


def test_sweep_removes_marked_objects(gc: StorageGarbageCollector, object_storage: ObjectStorageEngine):
    object_storage.put("orphan.bin", b"data")
    gc.scan()
    gc.mark()

    report = gc.sweep()

    assert isinstance(report, CleanupReport)
    assert report.objects_removed == 1
    assert report.bytes_reclaimed == len(b"data")
    assert object_storage.exists("orphan.bin") is False


def test_sweep_dry_run_does_not_delete(gc: StorageGarbageCollector, object_storage: ObjectStorageEngine):
    object_storage.put("orphan.bin", b"data")
    gc.scan()
    gc.mark()

    report = gc.sweep(dry_run=True)

    assert report.dry_run is True
    assert report.objects_removed == 0
    assert object_storage.exists("orphan.bin") is True


def test_run_performs_full_scan_mark_sweep_pipeline(gc: StorageGarbageCollector, object_storage: ObjectStorageEngine):
    object_storage.put("orphan.bin", b"data")

    report = gc.run()

    assert report.objects_removed == 1
    assert object_storage.exists("orphan.bin") is False


def test_report_returns_none_before_any_sweep(gc: StorageGarbageCollector):
    assert gc.report() is None


def test_report_returns_last_sweep_result(gc: StorageGarbageCollector, object_storage: ObjectStorageEngine):
    object_storage.put("orphan.bin", b"data")

    run_report = gc.run()

    assert gc.report() is run_report


def test_api_dry_run_and_run(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("orphan.bin", b"data")

    dry_response = client.post("/storage/gc/dry-run")
    assert dry_response.status_code == 200
    assert dry_response.json()["dry_run"] is True
    assert object_storage.exists("orphan.bin") is True

    run_response = client.post("/storage/gc/run")
    assert run_response.status_code == 200
    assert run_response.json()["objects_removed"] == 1
    assert object_storage.exists("orphan.bin") is False


def test_api_report_returns_404_before_sweep(client: TestClient):
    response = client.get("/storage/gc/report")
    assert response.status_code == 404


def test_api_report_after_run(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("orphan.bin", b"data")
    client.post("/storage/gc/run")

    response = client.get("/storage/gc/report")

    assert response.status_code == 200
    assert response.json()["objects_removed"] == 1


def test_api_candidates_endpoint(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("orphan.bin", b"data")

    response = client.get("/storage/gc/candidates")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["target"] == "orphaned_object"
