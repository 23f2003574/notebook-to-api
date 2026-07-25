from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_capacity import (
    DeploymentCapacityMonitor,
    ResourceDefinition,
)
from backend.governance.deployment_insights import (
    DeploymentInsightsService,
    UnknownDeploymentError,
    router as deployment_insights_router,
)
from backend.governance.deployment_metrics import (
    DEPLOY_DURATION_MS,
    FAILURE_COUNT,
    SUCCESS_COUNT,
    DeploymentMetricsCollector,
)
from backend.governance.deployment_recovery import (
    DeploymentRecoveryCoordinator,
    RecoveryStrategy,
)
from backend.governance.deployment_slo import DeploymentSLOManager, SLOObjective

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def service() -> DeploymentInsightsService:
    return DeploymentInsightsService()


def test_analyze_requires_deployment_identifier(
    service: DeploymentInsightsService,
):
    with pytest.raises(ValueError):
        service.analyze("")


def test_analyze_generates_reliability_insight_from_slo(
    service: DeploymentInsightsService,
):
    slo_manager = DeploymentSLOManager(objectives=[])
    slo_manager.register(
        SLOObjective(
            name="deployment_success_rate",
            target=0.99,
            comparator="gte",
            metric=SUCCESS_COUNT,
        )
    )
    metrics_collector = DeploymentMetricsCollector()
    metrics_collector.increment(SUCCESS_COUNT, amount=0.5)
    slo_manager.evaluate(metrics_collector.snapshot(), timestamp=BASE_TIME)

    report = service.analyze("svc-a", slo_manager=slo_manager, timestamp=BASE_TIME)

    categories = {i.category for i in report.insights}
    assert "RELIABILITY" in categories


def test_analyze_generates_capacity_insight_from_monitor(
    service: DeploymentInsightsService,
):
    capacity_monitor = DeploymentCapacityMonitor(resources=[])
    capacity_monitor.register_resource(
        ResourceDefinition(
            name="cpu", capacity=100.0, warning_threshold=0.5, critical_threshold=0.9
        )
    )
    capacity_monitor.collect("cpu", 60.0, timestamp=BASE_TIME)

    report = service.analyze(
        "svc-a", capacity_monitor=capacity_monitor, timestamp=BASE_TIME
    )

    [insight] = report.insights
    assert insight.category == "CAPACITY"
    assert insight.severity == "WARNING"


def test_analyze_generates_recovery_insight_from_failed_recovery(
    service: DeploymentInsightsService,
):
    recovery_coordinator = DeploymentRecoveryCoordinator(strategies=[])

    class _AlwaysFails(RecoveryStrategy):
        def execute(self, context):
            raise RuntimeError("rollback unavailable")

    recovery_coordinator.register_strategy(_AlwaysFails("only"))
    recovery_coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)

    report = service.analyze(
        "svc-a", recovery_coordinator=recovery_coordinator, timestamp=BASE_TIME
    )

    [insight] = report.insights
    assert insight.category == "RECOVERY"
    assert insight.severity == "CRITICAL"
    assert "rollback unavailable" in insight.summary


def test_analyze_detects_latency_trend_across_successive_calls(
    service: DeploymentInsightsService,
):
    collector_low = DeploymentMetricsCollector()
    collector_low.observe(DEPLOY_DURATION_MS, 100.0)
    service.analyze("svc-a", metrics_collector=collector_low, timestamp=BASE_TIME)

    collector_high = DeploymentMetricsCollector()
    collector_high.observe(DEPLOY_DURATION_MS, 500.0)
    report = service.analyze(
        "svc-a", metrics_collector=collector_high, timestamp=BASE_TIME
    )

    trend_insights = [i for i in report.insights if i.category == "PERFORMANCE"]
    assert any("trending upward" in i.summary for i in trend_insights)


def test_analyze_detects_latency_anomaly(service: DeploymentInsightsService):
    collector = DeploymentMetricsCollector()
    collector.observe(DEPLOY_DURATION_MS, 100.0)
    collector.observe(DEPLOY_DURATION_MS, 100.0)
    collector.observe(DEPLOY_DURATION_MS, 100.0)
    collector.observe(DEPLOY_DURATION_MS, 100.0)
    collector.observe(DEPLOY_DURATION_MS, 5000.0)

    report = service.analyze("svc-a", metrics_collector=collector, timestamp=BASE_TIME)

    assert any("spike" in i.summary for i in report.insights)


def test_analyze_detects_failure_rate_trend(service: DeploymentInsightsService):
    collector_stable = DeploymentMetricsCollector()
    collector_stable.increment(SUCCESS_COUNT, amount=10)
    service.analyze("svc-a", metrics_collector=collector_stable, timestamp=BASE_TIME)

    collector_failing = DeploymentMetricsCollector()
    collector_failing.increment(SUCCESS_COUNT, amount=5)
    collector_failing.increment(FAILURE_COUNT, amount=5)
    report = service.analyze(
        "svc-a", metrics_collector=collector_failing, timestamp=BASE_TIME
    )

    trend_insights = [i for i in report.insights if i.category == "DEPLOYMENT_TRENDS"]
    assert len(trend_insights) == 1


def test_recommend_returns_recommendations_from_latest_report(
    service: DeploymentInsightsService,
):
    capacity_monitor = DeploymentCapacityMonitor(resources=[])
    capacity_monitor.register_resource(
        ResourceDefinition(
            name="cpu", capacity=100.0, warning_threshold=0.5, critical_threshold=0.9
        )
    )
    capacity_monitor.collect("cpu", 60.0, timestamp=BASE_TIME)
    service.analyze("svc-a", capacity_monitor=capacity_monitor, timestamp=BASE_TIME)

    recommendations = service.recommend("svc-a")

    assert len(recommendations) == 1
    assert "cpu" in recommendations[0]


def test_recommend_unknown_deployment_raises(service: DeploymentInsightsService):
    with pytest.raises(UnknownDeploymentError):
        service.recommend("does-not-exist")


def test_summary_counts_insights_by_category_and_severity(
    service: DeploymentInsightsService,
):
    capacity_monitor = DeploymentCapacityMonitor(resources=[])
    capacity_monitor.register_resource(
        ResourceDefinition(
            name="cpu", capacity=100.0, warning_threshold=0.5, critical_threshold=0.9
        )
    )
    capacity_monitor.collect("cpu", 60.0, timestamp=BASE_TIME)
    service.analyze("svc-a", capacity_monitor=capacity_monitor, timestamp=BASE_TIME)

    summary = service.summary("svc-a")

    assert summary["total_insights"] == 1
    assert summary["by_category"]["CAPACITY"] == 1
    assert summary["by_severity"]["WARNING"] == 1


def test_summary_without_deployment_aggregates_all_latest(
    service: DeploymentInsightsService,
):
    capacity_monitor = DeploymentCapacityMonitor(resources=[])
    capacity_monitor.register_resource(
        ResourceDefinition(
            name="cpu", capacity=100.0, warning_threshold=0.5, critical_threshold=0.9
        )
    )
    capacity_monitor.collect("cpu", 60.0, timestamp=BASE_TIME)
    service.analyze("svc-a", capacity_monitor=capacity_monitor, timestamp=BASE_TIME)
    service.analyze("svc-b", capacity_monitor=capacity_monitor, timestamp=BASE_TIME)

    summary = service.summary()

    assert summary["total_insights"] == 2


def test_history_filters_by_deployment(service: DeploymentInsightsService):
    service.analyze("svc-a", timestamp=BASE_TIME)
    service.analyze("svc-b", timestamp=BASE_TIME)

    assert len(service.history()) == 2
    assert len(service.history(deployment="svc-a")) == 1


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_insights_router)
    return TestClient(app)


def test_api_analyze_and_summary(client: TestClient):
    analyze_response = client.post(
        "/governance/insights/analyze", json={"deployment": "svc-api-1"}
    )
    summary_response = client.get(
        "/governance/insights/summary", params={"deployment": "svc-api-1"}
    )

    assert analyze_response.status_code == 200
    assert analyze_response.json()["deployment"] == "svc-api-1"
    assert summary_response.status_code == 200


def test_api_analyze_requires_deployment(client: TestClient):
    response = client.post("/governance/insights/analyze", json={})

    assert response.status_code == 422


def test_api_history(client: TestClient):
    client.post("/governance/insights/analyze", json={"deployment": "svc-api-2"})

    response = client.get(
        "/governance/insights/history", params={"deployment": "svc-api-2"}
    )

    assert response.status_code == 200
    assert any(r["deployment"] == "svc-api-2" for r in response.json())
