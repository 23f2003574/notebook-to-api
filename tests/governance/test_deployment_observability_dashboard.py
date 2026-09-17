from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_alerts import AlertRule, DeploymentAlertManager
from backend.governance.deployment_capacity import (
    DeploymentCapacityMonitor,
    ResourceDefinition,
)
from backend.governance.deployment_diagnostics import DeploymentDiagnosticsService
from backend.governance.deployment_insights import DeploymentInsightsService
from backend.governance.deployment_metrics import (
    FAILURE_COUNT,
    DeploymentMetricsCollector,
)
from backend.governance.deployment_observability_dashboard import (
    DeploymentObservabilityDashboard,
    router as deployment_observability_dashboard_router,
)
from backend.governance.deployment_recovery import DeploymentRecoveryCoordinator
from backend.governance.deployment_slo import DeploymentSLOManager, SLOObjective

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


def test_overview_aggregates_metrics_alerts_slo_capacity_and_insights():
    metrics_collector = DeploymentMetricsCollector()
    metrics_collector.increment(FAILURE_COUNT, amount=1)

    alert_manager = DeploymentAlertManager(rules=[])
    alert_manager.register_rule(
        AlertRule(
            name="svc-a",
            level="ERROR",
            threshold=1,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )
    alert_manager.evaluate(metrics_collector.snapshot(), timestamp=BASE_TIME)

    capacity_monitor = DeploymentCapacityMonitor(resources=[])
    capacity_monitor.register_resource(ResourceDefinition(name="cpu", capacity=100.0))
    capacity_monitor.collect("cpu", 10.0, timestamp=BASE_TIME)

    dashboard = DeploymentObservabilityDashboard(
        metrics_collector=metrics_collector,
        alert_manager=alert_manager,
        capacity_monitor=capacity_monitor,
    )

    snapshot = dashboard.overview(timestamp=BASE_TIME)

    assert snapshot.sections["metrics"] is not None
    assert len(snapshot.sections["alerts"]) == 1
    assert "cpu" in snapshot.sections["capacity"]


def test_overview_is_cached_until_refresh():
    metrics_collector = DeploymentMetricsCollector()
    dashboard = DeploymentObservabilityDashboard(metrics_collector=metrics_collector)

    first = dashboard.overview(timestamp=BASE_TIME)
    metrics_collector.increment(FAILURE_COUNT, amount=1)
    second = dashboard.overview(timestamp=BASE_TIME)

    assert first is second
    assert second.sections["metrics"]["counters"] == {}


def test_refresh_recomputes_the_snapshot():
    metrics_collector = DeploymentMetricsCollector()
    dashboard = DeploymentObservabilityDashboard(metrics_collector=metrics_collector)

    dashboard.overview(timestamp=BASE_TIME)
    metrics_collector.increment(FAILURE_COUNT, amount=1)
    refreshed = dashboard.refresh(timestamp=BASE_TIME)

    assert refreshed.sections["metrics"]["counters"][FAILURE_COUNT] == 1.0
    assert dashboard.overview(timestamp=BASE_TIME) is refreshed


def test_deployment_drill_down_includes_diagnostics_alerts_recovery_insights():
    metrics_collector = DeploymentMetricsCollector()
    metrics_collector.increment(FAILURE_COUNT, amount=1)

    alert_manager = DeploymentAlertManager(rules=[])
    alert_manager.register_rule(
        AlertRule(
            name="svc-a",
            level="ERROR",
            threshold=1,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )
    alert_manager.evaluate(metrics_collector.snapshot(), timestamp=BASE_TIME)

    recovery_coordinator = DeploymentRecoveryCoordinator()
    recovery_coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)

    diagnostics_service = DeploymentDiagnosticsService()
    insights_service = DeploymentInsightsService()

    dashboard = DeploymentObservabilityDashboard(
        metrics_collector=metrics_collector,
        alert_manager=alert_manager,
        recovery_coordinator=recovery_coordinator,
        diagnostics_service=diagnostics_service,
        insights_service=insights_service,
    )

    result = dashboard.deployment("svc-a", timestamp=BASE_TIME)

    assert result["deployment"] == "svc-a"
    assert result["sections"]["diagnostics"] is not None
    assert len(result["sections"]["alerts"]) == 1
    assert result["sections"]["recovery"]["status"] == "SUCCEEDED"
    assert "total_insights" in result["sections"]["insights"]


def test_deployment_drill_down_handles_unknown_recovery_gracefully():
    recovery_coordinator = DeploymentRecoveryCoordinator()
    dashboard = DeploymentObservabilityDashboard(
        recovery_coordinator=recovery_coordinator
    )

    result = dashboard.deployment("svc-never-deployed", timestamp=BASE_TIME)

    assert result["sections"]["recovery"] is None


def test_deployment_requires_identifier():
    dashboard = DeploymentObservabilityDashboard()

    with pytest.raises(ValueError):
        dashboard.deployment("")


def test_health_reports_healthy_when_nothing_is_wrong():
    dashboard = DeploymentObservabilityDashboard()

    result = dashboard.health()

    assert result["status"] == "HEALTHY"
    assert result["active_alerts"] == 0


def test_health_reports_critical_on_critical_alert():
    metrics_collector = DeploymentMetricsCollector()
    metrics_collector.increment(FAILURE_COUNT, amount=1)
    alert_manager = DeploymentAlertManager(rules=[])
    alert_manager.register_rule(
        AlertRule(
            name="svc-a",
            level="CRITICAL",
            threshold=1,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )
    alert_manager.evaluate(metrics_collector.snapshot(), timestamp=BASE_TIME)

    dashboard = DeploymentObservabilityDashboard(alert_manager=alert_manager)

    result = dashboard.health()

    assert result["status"] == "CRITICAL"


def test_health_reports_warning_on_slo_at_risk():
    slo_manager = DeploymentSLOManager(objectives=[])
    slo_manager.register(
        SLOObjective(name="latency", target=1000.0, comparator="lte", metric="latency_ms")
    )
    metrics_collector = DeploymentMetricsCollector()
    metrics_collector.record("latency_ms", 5000.0)
    slo_manager.evaluate(metrics_collector.snapshot(), timestamp=BASE_TIME)

    dashboard = DeploymentObservabilityDashboard(slo_manager=slo_manager)

    result = dashboard.health()

    assert result["status"] == "CRITICAL"
    assert result["slo_breaches"] == 1


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_observability_dashboard_router)
    return TestClient(app)


def test_api_overview(client: TestClient):
    response = client.get("/governance/observability")

    assert response.status_code == 200
    assert "sections" in response.json()


def test_api_overview_refresh_query_param(client: TestClient):
    first = client.get("/governance/observability")
    second = client.get("/governance/observability", params={"refresh": "true"})

    assert first.status_code == 200
    assert second.status_code == 200


def test_api_health(client: TestClient):
    response = client.get("/governance/observability/health")

    assert response.status_code == 200
    assert "status" in response.json()


def test_api_deployment_drill_down(client: TestClient):
    response = client.get("/governance/observability/deployments/svc-api-1")

    assert response.status_code == 200
    assert response.json()["deployment"] == "svc-api-1"
