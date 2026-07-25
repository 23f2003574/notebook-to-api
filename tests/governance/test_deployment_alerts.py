from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_alerts import (
    AlertRule,
    DeploymentAlertManager,
    UnknownAlertError,
    router as deployment_alerts_router,
)
from backend.governance.deployment_metrics import (
    DEPLOY_DURATION_MS,
    FAILURE_COUNT,
    ROLLBACK_COUNT,
    DeploymentMetricsCollector,
)

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def manager() -> DeploymentAlertManager:
    return DeploymentAlertManager(rules=[])


def _snapshot(collector: DeploymentMetricsCollector):
    return collector.snapshot()


def test_register_rule_adds_a_custom_rule(manager: DeploymentAlertManager):
    collector = DeploymentMetricsCollector()
    collector.increment(FAILURE_COUNT, amount=5)
    manager.register_rule(
        AlertRule(
            name="custom_failure",
            level="ERROR",
            threshold=3,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )

    triggered = manager.evaluate(_snapshot(collector), timestamp=BASE_TIME)

    assert [alert.rule_name for alert in triggered] == ["custom_failure"]


def test_evaluate_generates_alert_when_threshold_breached(
    manager: DeploymentAlertManager,
):
    collector = DeploymentMetricsCollector()
    collector.increment(ROLLBACK_COUNT, amount=3)
    manager.register_rule(
        AlertRule(
            name="rollback_threshold",
            level="CRITICAL",
            threshold=3,
            comparator="gte",
            metric=ROLLBACK_COUNT,
        )
    )

    triggered = manager.evaluate(_snapshot(collector), timestamp=BASE_TIME)

    assert len(triggered) == 1
    alert = triggered[0]
    assert alert.level == "CRITICAL"
    assert alert.value == 3
    assert alert.is_active


def test_evaluate_skips_rule_below_threshold(manager: DeploymentAlertManager):
    collector = DeploymentMetricsCollector()
    collector.increment(FAILURE_COUNT, amount=0)
    manager.register_rule(
        AlertRule(
            name="deployment_failure",
            level="ERROR",
            threshold=1,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )

    triggered = manager.evaluate(_snapshot(collector), timestamp=BASE_TIME)

    assert triggered == []


def test_evaluate_deduplicates_active_alerts_for_same_rule(
    manager: DeploymentAlertManager,
):
    collector = DeploymentMetricsCollector()
    collector.increment(FAILURE_COUNT, amount=1)
    manager.register_rule(
        AlertRule(
            name="deployment_failure",
            level="ERROR",
            threshold=1,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )

    first_pass = manager.evaluate(_snapshot(collector), timestamp=BASE_TIME)
    second_pass = manager.evaluate(_snapshot(collector), timestamp=BASE_TIME)

    assert len(first_pass) == 1
    assert second_pass == []
    assert len(manager.active_alerts()) == 1


def test_resolve_marks_alert_inactive_and_allows_retrigger(
    manager: DeploymentAlertManager,
):
    collector = DeploymentMetricsCollector()
    collector.increment(FAILURE_COUNT, amount=1)
    manager.register_rule(
        AlertRule(
            name="deployment_failure",
            level="ERROR",
            threshold=1,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )

    [alert] = manager.evaluate(_snapshot(collector), timestamp=BASE_TIME)
    resolved = manager.resolve(alert.alert_id, timestamp=BASE_TIME)

    assert resolved.is_active is False
    assert manager.active_alerts() == []

    retriggered = manager.evaluate(_snapshot(collector), timestamp=BASE_TIME)
    assert len(retriggered) == 1
    assert retriggered[0].alert_id != alert.alert_id


def test_resolve_unknown_alert_raises(manager: DeploymentAlertManager):
    with pytest.raises(UnknownAlertError):
        manager.resolve("does-not-exist")


def test_default_rules_include_error_rate_and_high_latency():
    manager = DeploymentAlertManager()
    collector = DeploymentMetricsCollector()
    collector.increment("success_count", amount=1)
    collector.increment(FAILURE_COUNT, amount=3)
    collector.observe(DEPLOY_DURATION_MS, 45000.0)

    triggered = manager.evaluate(_snapshot(collector), timestamp=BASE_TIME)

    names = {alert.rule_name for alert in triggered}
    assert "error_rate" in names
    assert "high_latency" in names
    assert "deployment_failure" in names


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_alerts_router)
    return TestClient(app)


def test_api_evaluate_and_list_active_alerts(client: TestClient):
    from backend.governance.deployment_alerts import get_deployment_alert_manager
    from backend.governance.deployment_metrics import (
        get_deployment_metrics_collector,
    )

    get_deployment_metrics_collector().clear()
    get_deployment_alert_manager()._alerts.clear()
    get_deployment_metrics_collector().increment(ROLLBACK_COUNT, amount=3)

    evaluate_response = client.post("/governance/alerts/evaluate")
    assert evaluate_response.status_code == 200
    assert len(evaluate_response.json()) >= 1

    list_response = client.get("/governance/alerts")
    assert list_response.status_code == 200
    assert len(list_response.json()) >= 1


def test_api_resolve_alert(client: TestClient):
    from backend.governance.deployment_alerts import get_deployment_alert_manager
    from backend.governance.deployment_metrics import (
        get_deployment_metrics_collector,
    )

    get_deployment_metrics_collector().clear()
    manager = get_deployment_alert_manager()
    manager._alerts.clear()
    get_deployment_metrics_collector().increment(FAILURE_COUNT, amount=1)
    triggered = client.post("/governance/alerts/evaluate").json()
    alert = triggered[0]

    response = client.post(f"/governance/alerts/{alert['alert_id']}/resolve")

    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_api_resolve_unknown_alert_returns_404(client: TestClient):
    response = client.post("/governance/alerts/does-not-exist/resolve")

    assert response.status_code == 404
