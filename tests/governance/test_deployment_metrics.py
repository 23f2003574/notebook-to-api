from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_metrics import (
    DEPLOY_COUNT,
    DEPLOY_DURATION_MS,
    FAILURE_COUNT,
    SUCCESS_COUNT,
    DeploymentMetricsCollector,
    router as deployment_metrics_router,
)
from backend.governance.deployment_tracing import DeploymentTracingService

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def collector() -> DeploymentMetricsCollector:
    return DeploymentMetricsCollector()


def test_increment_accumulates_counter(collector: DeploymentMetricsCollector):
    collector.increment(DEPLOY_COUNT)
    collector.increment(DEPLOY_COUNT, amount=2.0)

    snapshot = collector.snapshot()

    assert snapshot.counters[DEPLOY_COUNT] == 3.0


def test_increment_rejects_negative_amount(
    collector: DeploymentMetricsCollector,
):
    with pytest.raises(ValueError):
        collector.increment(DEPLOY_COUNT, amount=-1.0)


def test_record_sets_gauge_value(collector: DeploymentMetricsCollector):
    collector.record("active_deployments", 3)
    collector.record("active_deployments", 5)

    snapshot = collector.snapshot()

    assert snapshot.gauges["active_deployments"] == 5


def test_observe_records_duration_into_histogram(
    collector: DeploymentMetricsCollector,
):
    collector.observe(DEPLOY_DURATION_MS, 100.0)
    collector.observe(DEPLOY_DURATION_MS, 300.0)

    snapshot = collector.snapshot()
    histogram = snapshot.histograms[DEPLOY_DURATION_MS]

    assert histogram.count == 2
    assert histogram.sum == 400.0
    assert histogram.min == 100.0
    assert histogram.max == 300.0
    assert histogram.avg == 200.0


def test_snapshot_is_immutable_point_in_time_view(
    collector: DeploymentMetricsCollector,
):
    collector.increment(DEPLOY_COUNT)
    snapshot = collector.snapshot()

    collector.increment(DEPLOY_COUNT)

    assert snapshot.counters[DEPLOY_COUNT] == 1.0
    assert snapshot.timestamp.tzinfo == timezone.utc


def test_snapshot_to_dict_serializes_all_metric_kinds(
    collector: DeploymentMetricsCollector,
):
    collector.increment(SUCCESS_COUNT)
    collector.record("active_deployments", 1)
    collector.observe(DEPLOY_DURATION_MS, 50.0)

    payload = collector.snapshot().to_dict()

    assert payload["counters"][SUCCESS_COUNT] == 1.0
    assert payload["gauges"]["active_deployments"] == 1
    assert payload["histograms"][DEPLOY_DURATION_MS]["count"] == 1


def test_tracing_root_span_records_deploy_metrics():
    tracing_service = DeploymentTracingService()
    collector = DeploymentMetricsCollector()

    root = tracing_service.start_trace(
        "deploy_rollout", timestamp=BASE_TIME, metrics_collector=collector
    )
    tracing_service.finish_span(
        root.trace_id,
        root.span_id,
        status="OK",
        timestamp=BASE_TIME + timedelta(seconds=2),
        metrics_collector=collector,
    )

    snapshot = collector.snapshot()

    assert snapshot.counters[DEPLOY_COUNT] == 1.0
    assert snapshot.counters[SUCCESS_COUNT] == 1.0
    assert snapshot.histograms[DEPLOY_DURATION_MS].sum == 2000.0


def test_tracing_child_span_does_not_record_deploy_metrics():
    tracing_service = DeploymentTracingService()
    collector = DeploymentMetricsCollector()

    root = tracing_service.start_trace(
        "deploy_rollout", timestamp=BASE_TIME, metrics_collector=collector
    )
    child = tracing_service.create_span(
        root.trace_id, "sub_step", parent_span_id=root.span_id
    )
    tracing_service.finish_span(
        child.trace_id, child.span_id, metrics_collector=collector
    )

    snapshot = collector.snapshot()

    assert DEPLOY_DURATION_MS not in snapshot.histograms
    assert FAILURE_COUNT not in snapshot.counters


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_metrics_router)
    return TestClient(app)


def test_api_list_metrics_returns_flattened_values(client: TestClient):
    from backend.governance.deployment_metrics import (
        get_deployment_metrics_collector,
    )

    metrics_collector = get_deployment_metrics_collector()
    metrics_collector.clear()
    metrics_collector.increment(DEPLOY_COUNT)
    metrics_collector.observe(DEPLOY_DURATION_MS, 10.0)

    response = client.get("/governance/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body[DEPLOY_COUNT] == 1.0
    assert body[f"{DEPLOY_DURATION_MS}_count"] == 1


def test_api_metrics_snapshot_returns_structured_payload(client: TestClient):
    from backend.governance.deployment_metrics import (
        get_deployment_metrics_collector,
    )

    metrics_collector = get_deployment_metrics_collector()
    metrics_collector.clear()
    metrics_collector.increment(DEPLOY_COUNT)

    response = client.get("/governance/metrics/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert "timestamp" in body
    assert body["counters"][DEPLOY_COUNT] == 1.0
