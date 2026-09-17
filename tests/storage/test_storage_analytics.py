import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.storage.artifact_manager import ArtifactManager, ArtifactType
from backend.storage.object_storage import ObjectStorageEngine
from backend.storage.storage_analytics import (
    StorageAnalyticsService,
    StorageMetrics,
    StorageTrend,
    get_storage_analytics_service,
    router as storage_analytics_router,
)
from backend.storage.storage_gc import StorageGarbageCollector
from backend.storage.storage_replication import StorageReplicationEngine


@pytest.fixture
def object_storage() -> ObjectStorageEngine:
    return ObjectStorageEngine()


@pytest.fixture
def artifact_manager(object_storage: ObjectStorageEngine) -> ArtifactManager:
    return ArtifactManager(object_storage=object_storage)


@pytest.fixture
def replication_engine(object_storage: ObjectStorageEngine) -> StorageReplicationEngine:
    return StorageReplicationEngine(primary=object_storage)


@pytest.fixture
def gc(object_storage: ObjectStorageEngine, artifact_manager: ArtifactManager) -> StorageGarbageCollector:
    return StorageGarbageCollector(object_storage=object_storage, artifact_manager=artifact_manager)


@pytest.fixture
def service(
    object_storage: ObjectStorageEngine,
    artifact_manager: ArtifactManager,
    replication_engine: StorageReplicationEngine,
    gc: StorageGarbageCollector,
) -> StorageAnalyticsService:
    return StorageAnalyticsService(
        object_storage=object_storage,
        artifact_manager=artifact_manager,
        replication_engine=replication_engine,
        gc=gc,
    )


@pytest.fixture
def client(service: StorageAnalyticsService) -> TestClient:
    app = FastAPI()
    app.include_router(storage_analytics_router)
    app.dependency_overrides[get_storage_analytics_service] = lambda: service
    return TestClient(app)


def test_record_captures_current_state(service: StorageAnalyticsService, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    metrics = service.record()

    assert isinstance(metrics, StorageMetrics)
    assert metrics.object_count == 1
    assert metrics.storage_usage_bytes == len(b"hello")


def test_record_tracks_artifact_count(service: StorageAnalyticsService, artifact_manager: ArtifactManager):
    artifact_manager.create("model.pkl", ArtifactType.MODEL, b"data")

    metrics = service.record()

    assert metrics.artifact_count == 1


def test_record_computes_replication_rate(
    service: StorageAnalyticsService, object_storage: ObjectStorageEngine, replication_engine: StorageReplicationEngine
):
    replica = ObjectStorageEngine()
    replication_engine.add_replica("replica-a", replica)
    object_storage.put("a.txt", b"hello")
    replication_engine.replicate("a.txt")

    metrics = service.record()

    assert metrics.replication_rate == 1.0


def test_record_without_replication_engine_defaults_to_zero(
    object_storage: ObjectStorageEngine, artifact_manager: ArtifactManager
):
    service = StorageAnalyticsService(object_storage=object_storage, artifact_manager=artifact_manager)

    metrics = service.record()

    assert metrics.replication_rate == 0.0


def test_record_computes_gc_efficiency(
    service: StorageAnalyticsService, object_storage: ObjectStorageEngine, gc: StorageGarbageCollector
):
    object_storage.put("orphan.bin", b"data")
    gc.run()

    metrics = service.record()

    assert metrics.gc_efficiency == 1.0


def test_summary_records_first_snapshot_if_none_exists(service: StorageAnalyticsService):
    summary = service.summary()

    assert isinstance(summary, StorageMetrics)


def test_summary_returns_latest_without_recording_again(
    service: StorageAnalyticsService, object_storage: ObjectStorageEngine
):
    first = service.record()
    object_storage.put("a.txt", b"hello")

    summary = service.summary()

    assert summary is first
    assert summary.object_count == 0


def test_trends_empty_with_fewer_than_two_snapshots(service: StorageAnalyticsService):
    service.record()

    assert service.trends() == []


def test_trends_reports_increasing_object_count(service: StorageAnalyticsService, object_storage: ObjectStorageEngine):
    service.record()
    object_storage.put("a.txt", b"hello")
    service.record()

    trends = service.trends(metric="object_count")

    assert len(trends) == 1
    trend = trends[0]
    assert isinstance(trend, StorageTrend)
    assert trend.metric == "object_count"
    assert trend.direction == "increasing"
    assert trend.change == 1


def test_trends_reports_flat_direction_when_unchanged(service: StorageAnalyticsService):
    service.record()
    service.record()

    trends = service.trends(metric="object_count")

    assert trends[0].direction == "flat"
    assert trends[0].change == 0


def test_trends_without_metric_returns_all_tracked_metrics(
    service: StorageAnalyticsService, object_storage: ObjectStorageEngine
):
    service.record()
    object_storage.put("a.txt", b"hello")
    service.record()

    trends = service.trends()

    assert {trend.metric for trend in trends} == {
        "storage_usage_bytes",
        "object_count",
        "artifact_count",
        "replication_rate",
        "gc_efficiency",
    }


def test_forecast_raises_with_fewer_than_two_snapshots(service: StorageAnalyticsService):
    service.record()

    with pytest.raises(ValueError):
        service.forecast()


def test_forecast_projects_linear_growth(service: StorageAnalyticsService, object_storage: ObjectStorageEngine):
    service.record()
    object_storage.put("a.txt", b"1234")
    service.record()
    object_storage.put("b.txt", b"5678")
    service.record()

    forecast = service.forecast(metric="object_count", periods_ahead=2)

    assert forecast["current"] == 2
    assert forecast["projected"] == 4


def test_forecast_rejects_non_positive_periods(service: StorageAnalyticsService):
    service.record()
    service.record()

    with pytest.raises(ValueError):
        service.forecast(periods_ahead=0)


def test_api_record_endpoint(client: TestClient, object_storage: ObjectStorageEngine):
    object_storage.put("a.txt", b"hello")

    response = client.get("/storage/analytics")

    assert response.status_code == 200
    assert response.json()["object_count"] == 1


def test_api_summary_endpoint(client: TestClient):
    response = client.get("/storage/analytics/summary")
    assert response.status_code == 200


def test_api_trends_endpoint(client: TestClient, object_storage: ObjectStorageEngine):
    client.get("/storage/analytics")
    object_storage.put("a.txt", b"hello")
    client.get("/storage/analytics")

    response = client.get("/storage/analytics/trends", params={"metric": "object_count"})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_forecast_endpoint_returns_422_with_insufficient_history(client: TestClient):
    response = client.get("/storage/analytics/forecast")
    assert response.status_code == 422


def test_api_forecast_endpoint(client: TestClient, object_storage: ObjectStorageEngine):
    client.get("/storage/analytics")
    object_storage.put("a.txt", b"hello")
    client.get("/storage/analytics")

    response = client.get("/storage/analytics/forecast", params={"metric": "object_count"})

    assert response.status_code == 200
    assert response.json()["metric"] == "object_count"
