import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.artifact_manager import ArtifactManager, ArtifactType
from backend.storage.dashboard import (
    StorageDashboardAPI,
    get_storage_dashboard_api,
    router as dashboard_router,
)
from backend.storage.lifecycle_policy import LifecyclePolicyManager, PolicyType, RetentionRule
from backend.storage.object_storage import ObjectStorageEngine
from backend.storage.storage_analytics import StorageAnalyticsService
from backend.storage.storage_registry import StorageMetadata, StorageRegistry
from backend.storage.storage_replication import StorageReplicationEngine


@pytest.fixture
def object_storage() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def artifact_manager(object_storage: ObjectStorageEngine) -> ArtifactManager:
    return ArtifactManager(object_storage=object_storage)


@pytest.fixture
def storage_registry() -> StorageRegistry:
    return StorageRegistry()


@pytest.fixture
def replication_engine(object_storage: ObjectStorageEngine) -> StorageReplicationEngine:
    return StorageReplicationEngine(primary=object_storage)


@pytest.fixture
def lifecycle_manager(artifact_manager: ArtifactManager) -> LifecyclePolicyManager:
    return LifecyclePolicyManager(artifact_manager=artifact_manager)


@pytest.fixture
def analytics_service(
    object_storage: ObjectStorageEngine, artifact_manager: ArtifactManager, replication_engine: StorageReplicationEngine
) -> StorageAnalyticsService:
    return StorageAnalyticsService(
        object_storage=object_storage, artifact_manager=artifact_manager, replication_engine=replication_engine
    )


@pytest.fixture
def dashboard(
    artifact_manager: ArtifactManager,
    analytics_service: StorageAnalyticsService,
    storage_registry: StorageRegistry,
    replication_engine: StorageReplicationEngine,
    lifecycle_manager: LifecyclePolicyManager,
) -> StorageDashboardAPI:
    return StorageDashboardAPI(
        artifact_manager=artifact_manager,
        analytics_service=analytics_service,
        storage_registry=storage_registry,
        replication_engine=replication_engine,
        lifecycle_manager=lifecycle_manager,
    )


@pytest.fixture
def client(dashboard: StorageDashboardAPI) -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router)
    app.dependency_overrides[get_storage_dashboard_api] = lambda: dashboard
    return TestClient(app)


def test_overview_reports_storage_and_replication(
    dashboard: StorageDashboardAPI, object_storage: ObjectStorageEngine, replication_engine: StorageReplicationEngine
):
    replica = ObjectStorageEngine()
    replication_engine.add_replica("replica-a", replica)
    object_storage.put("a.txt", b"hello")
    replication_engine.replicate("a.txt")

    overview = dashboard.overview()

    assert overview["storage"]["object_count"] == 1
    assert overview["replication"]["tracked_pairs"] == 1
    assert overview["replication"]["in_sync"] == 1


def test_overview_reports_lifecycle_policy_count(dashboard: StorageDashboardAPI, lifecycle_manager: LifecyclePolicyManager):
    lifecycle_manager.create_policy("expire-logs", PolicyType.EXPIRATION, RetentionRule(max_age_seconds=3600))

    overview = dashboard.overview()

    assert overview["lifecycle"]["policy_count"] == 1


def test_overview_without_optional_collaborators(artifact_manager: ArtifactManager, analytics_service: StorageAnalyticsService):
    minimal = StorageDashboardAPI(artifact_manager=artifact_manager, analytics_service=analytics_service)

    overview = minimal.overview()

    assert overview["replication"]["tracked_pairs"] == 0
    assert overview["lifecycle"]["policy_count"] == 0


def test_artifacts_groups_by_type_and_namespace(dashboard: StorageDashboardAPI, artifact_manager: ArtifactManager):
    artifact_manager.create("a.pkl", ArtifactType.MODEL, b"1", namespace="team-a")
    artifact_manager.create("b.pkl", ArtifactType.MODEL, b"2", namespace="team-b")
    artifact_manager.create("c.csv", ArtifactType.DATASET, b"3", namespace="team-a")

    result = dashboard.artifacts()

    assert result["total"] == 3
    assert result["by_type"] == {"model": 2, "dataset": 1}
    assert result["by_namespace"] == {"team-a": 2, "team-b": 1}
    assert len(result["artifacts"]) == 3


def test_capacity_reports_usage_and_backends(dashboard: StorageDashboardAPI, object_storage: ObjectStorageEngine, storage_registry: StorageRegistry):
    object_storage.put("a.txt", b"hello")
    storage_registry.register("backend-1", ["read"], StorageMetadata(kind="s3", region="us-east-1", version="1.0.0"))

    capacity = dashboard.capacity()

    assert capacity["storage_usage_bytes"] == len(b"hello")
    assert capacity["object_count"] == 1
    assert len(capacity["backends"]) == 1


def test_capacity_forecast_is_none_without_history(dashboard: StorageDashboardAPI):
    capacity = dashboard.capacity()

    assert capacity["forecast"] is None


def test_capacity_forecast_present_with_history(dashboard: StorageDashboardAPI, analytics_service: StorageAnalyticsService, object_storage: ObjectStorageEngine):
    analytics_service.record()
    object_storage.put("a.txt", b"hello")
    analytics_service.record()

    capacity = dashboard.capacity()

    assert capacity["forecast"] is not None
    assert capacity["forecast"]["metric"] == "storage_usage_bytes"


def test_analytics_section_includes_summary_and_trends(dashboard: StorageDashboardAPI, analytics_service: StorageAnalyticsService, object_storage: ObjectStorageEngine):
    analytics_service.record()
    object_storage.put("a.txt", b"hello")
    analytics_service.record()

    result = dashboard.analytics()

    assert "summary" in result
    assert len(result["trends"]) == 5
    assert result["snapshot_count"] == 2


def test_api_overview_endpoint(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    response = client.get("/storage/dashboard")

    assert response.status_code == 200
    assert response.json()["storage"]["object_count"] == 1


def test_api_artifacts_endpoint(client: TestClient, artifact_manager: ArtifactManager):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    response = client.get("/storage/dashboard/artifacts")

    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_api_capacity_endpoint(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    response = client.get("/storage/dashboard/capacity")

    assert response.status_code == 200
    assert response.json()["storage_usage_bytes"] == len(b"hello")


def test_api_analytics_endpoint(client: TestClient):
    response = client.get("/storage/dashboard/analytics")

    assert response.status_code == 200
    assert "summary" in response.json()
