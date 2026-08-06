import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.artifact_manager import ArtifactType
from backend.storage.bootstrap import (
    REQUIRED_SERVICES,
    SUBSYSTEM_NAME,
    StorageBootstrap,
    StorageBootstrapError,
    StorageNotInitializedError,
    UnknownStorageServiceError,
    bootstrap_storage_subsystem,
    get_storage_bootstrap,
)
from backend.storage.lifecycle_policy import PolicyType, RetentionRule
from backend.storage.object_storage import ObjectStorageEngine


def test_register_services_wires_every_required_service():
    bootstrap = StorageBootstrap()

    services = bootstrap.register_services()

    assert set(services) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_register_call():
    bootstrap = StorageBootstrap()

    assert bootstrap.registered_services() == {}

    bootstrap.register_services()

    assert set(bootstrap.registered_services()) == set(REQUIRED_SERVICES)


def test_discover_returns_named_service():
    bootstrap = StorageBootstrap()
    bootstrap.register_services()

    artifact_manager = bootstrap.discover("artifact_manager")

    assert artifact_manager is bootstrap.registered_services()["artifact_manager"]


def test_discover_unknown_service_raises():
    bootstrap = StorageBootstrap()
    bootstrap.register_services()

    with pytest.raises(UnknownStorageServiceError):
        bootstrap.discover("does-not-exist")


def test_wire_components_registers_default_backend():
    bootstrap = StorageBootstrap()
    services = bootstrap.register_services()
    registry = services["storage_registry"]

    bootstrap.wire_components()

    assert registry.get("local") is not None
    assert registry.is_active("local") is True


def test_wire_components_restores_existing_artifacts():
    bootstrap = StorageBootstrap()
    services = bootstrap.register_services()
    artifact_manager = services["artifact_manager"]
    suffix = uuid.uuid4().hex
    artifact = artifact_manager.create(f"model-{suffix}.pkl", ArtifactType.MODEL, b"data")

    restored = bootstrap.wire_components()

    assert artifact.artifact_id in restored


def test_wire_components_is_idempotent():
    bootstrap = StorageBootstrap()
    bootstrap.register_services()

    first = bootstrap.wire_components()
    second = bootstrap.wire_components()

    assert first == second


def test_initialize_returns_valid_result():
    bootstrap = StorageBootstrap()

    result = bootstrap.initialize()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)
    assert result.missing_services == ()
    assert bootstrap.is_initialized is True


def test_initialize_raises_when_a_required_service_is_missing(monkeypatch):
    bootstrap = StorageBootstrap()
    incomplete = {name: object() for name in REQUIRED_SERVICES if name != "dashboard_api"}
    monkeypatch.setattr(bootstrap, "register_services", lambda: incomplete)
    monkeypatch.setattr(bootstrap, "wire_components", lambda: ())

    with pytest.raises(StorageBootstrapError) as exc_info:
        bootstrap.initialize()

    assert exc_info.value.result.missing_services == ("dashboard_api",)
    assert exc_info.value.result.valid is False
    assert bootstrap.is_initialized is False


def test_health_check_before_initialize_raises():
    bootstrap = StorageBootstrap()

    with pytest.raises(StorageNotInitializedError):
        bootstrap.health_check()


def test_health_check_delegates_to_the_dashboard():
    bootstrap = StorageBootstrap()
    bootstrap.initialize()

    report = bootstrap.health_check()

    assert report["status"] == "ok"
    assert "storage" in report
    assert "replication" in report


def test_shutdown_before_initialize_raises():
    bootstrap = StorageBootstrap()

    with pytest.raises(StorageNotInitializedError):
        bootstrap.shutdown()


def test_shutdown_resets_state():
    bootstrap = StorageBootstrap()
    bootstrap.initialize()

    bootstrap.shutdown()

    assert bootstrap.is_initialized is False


def test_bootstrap_storage_subsystem_is_valid():
    result = bootstrap_storage_subsystem()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)


def test_bootstrap_storage_subsystem_is_idempotent():
    first = bootstrap_storage_subsystem()
    second = bootstrap_storage_subsystem()

    assert first.valid is True
    assert second.valid is True


def test_get_storage_bootstrap_returns_singleton():
    assert get_storage_bootstrap() is get_storage_bootstrap()


def test_subsystem_name_is_stable():
    assert SUBSYSTEM_NAME == "storage_artifact_and_object_management"


def test_end_to_end_storage_workflow():
    result = bootstrap_storage_subsystem()
    assert result.valid is True

    services = get_storage_bootstrap().registered_services()
    object_storage = services["object_storage"]
    artifact_manager = services["artifact_manager"]
    blob_upload = services["blob_upload"]
    version_manager = services["version_manager"]
    integrity_engine = services["integrity_engine"]
    lifecycle_manager = services["lifecycle_manager"]
    replication_engine = services["replication_engine"]
    gc = services["garbage_collector"]
    analytics = services["analytics_service"]
    dashboard = services["dashboard_api"]
    export_service = services["export_service"]

    suffix = uuid.uuid4().hex
    namespace = f"e2e-{suffix}"

    artifact = artifact_manager.create(f"model-{suffix}.pkl", ArtifactType.MODEL, b"v1-data", namespace=namespace)

    integrity_report = integrity_engine.verify(artifact.object_key)
    assert integrity_report.status == "ok"

    version = version_manager.create_version(artifact.artifact_id, b"v2-data")
    refreshed = artifact_manager.fetch(artifact.artifact_id)
    assert refreshed.object_key == version.object_key

    upload_key = f"uploads/{suffix}/bundle.bin"
    session = blob_upload.create_upload(upload_key)
    blob_upload.upload_part(session.upload_id, 1, b"part-one-")
    blob_upload.upload_part(session.upload_id, 2, b"part-two")
    uploaded = blob_upload.complete_upload(session.upload_id)
    assert uploaded.data == b"part-one-part-two"

    replica = ObjectStorageEngine()
    replica_id = f"replica-{suffix}"
    replication_engine.add_replica(replica_id, replica)
    job = replication_engine.replicate(refreshed.object_key, [replica_id])
    assert job.status.value == "completed"
    assert replica.exists(refreshed.object_key) is True

    lifecycle_manager.create_policy(
        f"retain-{suffix}",
        PolicyType.RETENTION,
        RetentionRule(max_age_seconds=999999, namespace=namespace),
    )
    assert lifecycle_manager.evaluate(artifact.artifact_id) == []

    gc.run()
    assert object_storage.exists(refreshed.object_key) is True

    metrics = analytics.record()
    assert metrics.object_count >= 1

    overview = dashboard.overview()
    assert "storage" in overview

    export = export_service.export_artifacts(format="json")
    assert export.manifest.export_type == "artifacts"


@pytest.fixture
def client() -> TestClient:
    from backend.storage.storage_registry import router as storage_registry_router
    from backend.storage.object_storage import router as object_storage_router
    from backend.storage.artifact_manager import router as artifact_manager_router
    from backend.storage.blob_upload import router as blob_upload_router
    from backend.storage.storage_versioning import router as storage_versioning_router
    from backend.storage.artifact_integrity import router as artifact_integrity_router
    from backend.storage.lifecycle_policy import router as lifecycle_policy_router
    from backend.storage.storage_replication import router as storage_replication_router
    from backend.storage.storage_gc import router as storage_gc_router
    from backend.storage.storage_analytics import router as storage_analytics_router
    from backend.storage.dashboard import router as storage_dashboard_router
    from backend.storage.export_service import router as storage_export_router

    app = FastAPI()
    for router in (
        storage_registry_router,
        object_storage_router,
        artifact_manager_router,
        blob_upload_router,
        storage_versioning_router,
        artifact_integrity_router,
        lifecycle_policy_router,
        storage_replication_router,
        storage_gc_router,
        storage_analytics_router,
        storage_dashboard_router,
        storage_export_router,
    ):
        app.include_router(router)
    return TestClient(app)


def test_route_registration_covers_every_service_area(client: TestClient):
    bootstrap_storage_subsystem()
    suffix = uuid.uuid4().hex

    assert client.get("/storage/backends").status_code == 200

    put_response = client.put(f"/storage/objects/route-check-{suffix}.bin", content=b"data")
    assert put_response.status_code == 200
    assert client.get(f"/storage/objects/route-check-{suffix}.bin").status_code == 200

    assert client.get("/storage/artifacts").status_code == 200

    upload_response = client.post("/storage/uploads", params={"key": f"route-check-upload-{suffix}.bin"})
    assert upload_response.status_code == 200

    create_artifact_response = client.post(
        "/storage/artifacts",
        params={"name": f"route-{suffix}.bin", "artifact_type": "model"},
        content=b"data",
    )
    artifact_id = create_artifact_response.json()["artifact_id"]
    assert client.get(f"/storage/versions/{artifact_id}").status_code == 200

    assert client.get(f"/storage/integrity/route-check-{suffix}.bin").status_code == 200
    assert client.get("/storage/lifecycle").status_code == 200
    assert client.get("/storage/replication/status").status_code == 200
    assert client.get("/storage/gc/candidates").status_code == 200
    assert client.get("/storage/analytics").status_code == 200
    assert client.get("/storage/dashboard").status_code == 200
    assert client.get("/storage/export/artifacts").status_code == 200
