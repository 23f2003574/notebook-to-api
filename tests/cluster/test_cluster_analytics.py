from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.cluster_analytics import (
    ClusterAnalyticsService,
    ClusterMetrics,
    get_cluster_analytics_service,
    router as cluster_analytics_router,
)
from backend.cluster.execution_coordinator import ExecutionCoordinator
from backend.cluster.job_dispatcher import DistributedJobDispatcher
from backend.cluster.worker_discovery import WorkerDiscoveryService
from backend.cluster.worker_registry import WorkerMetadata, WorkerRegistry


def make_metadata(hostname: str = "node-a.local") -> WorkerMetadata:
    return WorkerMetadata(hostname=hostname, region="us-east-1", version="1.0.0")


@pytest.fixture
def service() -> ClusterAnalyticsService:
    return ClusterAnalyticsService()


@pytest.fixture
def client(service: ClusterAnalyticsService) -> TestClient:
    app = FastAPI()
    app.include_router(cluster_analytics_router)
    app.dependency_overrides[get_cluster_analytics_service] = lambda: service
    return TestClient(app)


def test_record_creates_metric(service: ClusterAnalyticsService):
    record = service.record("parse", worker_count=3, active_jobs=2, queue_depth=1)

    assert isinstance(record, ClusterMetrics)
    assert record.capability == "parse"
    assert record.worker_count == 3


def test_list_records_filters_by_capability(service: ClusterAnalyticsService):
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0)
    service.record("export", worker_count=1, active_jobs=1, queue_depth=0)

    listed = service.list_records("parse")

    assert len(listed) == 1
    assert listed[0].capability == "parse"


def test_summary_computes_worker_utilization(service: ClusterAnalyticsService):
    service.record("parse", worker_count=4, active_jobs=2, queue_depth=0)
    service.record("parse", worker_count=4, active_jobs=4, queue_depth=0)

    summary = service.summary("parse")

    assert summary["average_worker_utilization"] == pytest.approx(0.75)


def test_summary_computes_throughput_and_failure_rate(service: ClusterAnalyticsService):
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0, completed_count=3, failed_count=1)

    summary = service.summary("parse")

    assert summary["job_throughput"] == 3
    assert summary["failure_rate"] == pytest.approx(0.25)


def test_summary_computes_average_queue_depth(service: ClusterAnalyticsService):
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=2)
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=6)

    summary = service.summary("parse")

    assert summary["average_queue_depth"] == pytest.approx(4.0)


def test_summary_computes_average_scheduling_latency(service: ClusterAnalyticsService):
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0, scheduling_latency_ms=100.0)
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0, scheduling_latency_ms=300.0)

    summary = service.summary("parse")

    assert summary["average_scheduling_latency_ms"] == pytest.approx(200.0)


def test_summary_with_no_records_has_safe_defaults(service: ClusterAnalyticsService):
    summary = service.summary("does-not-exist")

    assert summary["sample_count"] == 0
    assert summary["average_worker_utilization"] == 0.0
    assert summary["failure_rate"] == 0.0
    assert summary["average_scheduling_latency_ms"] is None


def test_trends_buckets_by_day(service: ClusterAnalyticsService):
    day1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    day2 = day1 + timedelta(days=1)
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0, completed_count=1, recorded_at=day1)
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0, completed_count=1, recorded_at=day1)
    service.record("parse", worker_count=2, active_jobs=2, queue_depth=0, failed_count=1, recorded_at=day2)

    trends = service.trends("parse", bucket="day")

    assert len(trends) == 2
    assert trends[0].sample_count == 2
    assert trends[0].throughput == 2
    assert trends[1].failure_rate == pytest.approx(1.0)


def test_trends_rejects_unsupported_bucket(service: ClusterAnalyticsService):
    with pytest.raises(ValueError):
        service.trends(bucket="week")


def test_export_json_includes_summary_trends_and_records(service: ClusterAnalyticsService):
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0)

    exported = service.export("parse")

    assert "summary" in exported
    assert "trends" in exported
    assert len(exported["records"]) == 1


def test_export_csv_returns_string_with_header(service: ClusterAnalyticsService):
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0)

    exported = service.export("parse", format="csv")

    assert isinstance(exported, str)
    assert "capability" in exported.splitlines()[0]


def test_export_unsupported_format_raises(service: ClusterAnalyticsService):
    with pytest.raises(ValueError):
        service.export(format="xml")


def test_execution_coordinator_records_metric_on_completion(service: ClusterAnalyticsService):
    registry = WorkerRegistry()
    discovery = WorkerDiscoveryService(registry, stale_after_seconds=300.0)
    dispatcher = DistributedJobDispatcher(discovery)
    coordinator = ExecutionCoordinator(dispatcher, analytics=service)
    registry.register("worker-1", ["parse"], make_metadata())

    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1", result={"rows": 1})

    records = service.list_records("parse")
    assert len(records) == 1
    assert records[0].completed_count == 1
    assert records[0].scheduling_latency_ms is not None


def test_execution_coordinator_records_failure(service: ClusterAnalyticsService):
    registry = WorkerRegistry()
    discovery = WorkerDiscoveryService(registry, stale_after_seconds=300.0)
    dispatcher = DistributedJobDispatcher(discovery)
    coordinator = ExecutionCoordinator(dispatcher, analytics=service)
    registry.register("worker-1", ["parse"], make_metadata())

    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1", success=False, error="boom")

    records = service.list_records("parse")
    assert records[0].failed_count == 1


def test_execution_coordinator_without_analytics_does_not_record(service: ClusterAnalyticsService):
    registry = WorkerRegistry()
    discovery = WorkerDiscoveryService(registry, stale_after_seconds=300.0)
    dispatcher = DistributedJobDispatcher(discovery)
    coordinator = ExecutionCoordinator(dispatcher)
    registry.register("worker-1", ["parse"], make_metadata())

    coordinator.submit("job-1", "parse")
    coordinator.complete("job-1")

    assert service.list_records("parse") == []


def test_api_list_metrics(client: TestClient, service: ClusterAnalyticsService):
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0)

    response = client.get("/cluster/analytics")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_summary(client: TestClient, service: ClusterAnalyticsService):
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0)

    response = client.get("/cluster/analytics/summary", params={"capability": "parse"})

    assert response.status_code == 200
    assert response.json()["sample_count"] == 1


def test_api_trends(client: TestClient, service: ClusterAnalyticsService):
    service.record("parse", worker_count=2, active_jobs=1, queue_depth=0)

    response = client.get("/cluster/analytics/trends", params={"capability": "parse"})

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_trends_invalid_bucket_returns_422(client: TestClient):
    response = client.get("/cluster/analytics/trends", params={"bucket": "week"})

    assert response.status_code == 422
