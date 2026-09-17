from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_metrics import (
    DEPLOY_DURATION_MS,
    DeploymentMetricsCollector,
)
from backend.governance.deployment_slo import (
    DeploymentSLOManager,
    SLOObjective,
    UnknownObjectiveError,
    router as deployment_slo_router,
)

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def manager() -> DeploymentSLOManager:
    return DeploymentSLOManager(objectives=[])


def test_register_adds_an_objective(manager: DeploymentSLOManager):
    manager.register(
        SLOObjective(
            name="latency",
            target=1000.0,
            comparator="lte",
            metric=DEPLOY_DURATION_MS,
        )
    )

    assert [obj.name for obj in manager.list_objectives()] == ["latency"]


def test_remove_drops_an_objective(manager: DeploymentSLOManager):
    manager.register(
        SLOObjective(
            name="latency",
            target=1000.0,
            comparator="lte",
            metric=DEPLOY_DURATION_MS,
        )
    )

    manager.remove("latency")

    assert manager.list_objectives() == []


def test_remove_unknown_objective_raises(manager: DeploymentSLOManager):
    with pytest.raises(UnknownObjectiveError):
        manager.remove("does-not-exist")


def test_evaluate_marks_healthy_when_target_is_met(
    manager: DeploymentSLOManager,
):
    manager.register(
        SLOObjective(
            name="latency",
            target=1000.0,
            comparator="lte",
            metric=DEPLOY_DURATION_MS,
        )
    )
    collector = DeploymentMetricsCollector()
    collector.observe(DEPLOY_DURATION_MS, 200.0)

    [result] = manager.evaluate(collector.snapshot(), timestamp=BASE_TIME)

    assert result.status == "HEALTHY"
    assert result.value == 200.0


def test_evaluate_marks_breached_when_rolling_average_fails(
    manager: DeploymentSLOManager,
):
    manager.register(
        SLOObjective(
            name="latency",
            target=1000.0,
            comparator="lte",
            metric=DEPLOY_DURATION_MS,
            window_size=3,
        )
    )
    collector = DeploymentMetricsCollector()

    for value in (2000.0, 2500.0, 3000.0):
        collector.clear()
        collector.observe(DEPLOY_DURATION_MS, value)
        result = manager.evaluate(collector.snapshot(), timestamp=BASE_TIME)[0]

    assert result.status == "BREACHED"
    assert result.rolling_average == (2000.0 + 2500.0 + 3000.0) / 3


def test_evaluate_marks_at_risk_when_latest_fails_but_window_still_healthy(
    manager: DeploymentSLOManager,
):
    manager.register(
        SLOObjective(
            name="latency",
            target=1000.0,
            comparator="lte",
            metric=DEPLOY_DURATION_MS,
            window_size=5,
        )
    )
    collector = DeploymentMetricsCollector()

    for _ in range(4):
        collector.clear()
        collector.observe(DEPLOY_DURATION_MS, 10.0)
        manager.evaluate(collector.snapshot(), timestamp=BASE_TIME)
    collector.clear()
    collector.observe(DEPLOY_DURATION_MS, 4000.0)
    result = manager.evaluate(collector.snapshot(), timestamp=BASE_TIME)[0]

    assert result.status == "AT_RISK"
    assert result.rolling_average <= 1000.0


def test_rolling_window_respects_configured_size(
    manager: DeploymentSLOManager,
):
    manager.register(
        SLOObjective(
            name="latency",
            target=1000.0,
            comparator="lte",
            metric=DEPLOY_DURATION_MS,
            window_size=2,
        )
    )
    collector = DeploymentMetricsCollector()

    for value in (100.0, 200.0, 900.0):
        collector.clear()
        collector.observe(DEPLOY_DURATION_MS, value)
        result = manager.evaluate(collector.snapshot(), timestamp=BASE_TIME)[0]

    assert result.window_size == 2
    assert result.rolling_average == (200.0 + 900.0) / 2


def test_status_returns_latest_result_per_objective(
    manager: DeploymentSLOManager,
):
    manager.register(
        SLOObjective(
            name="latency",
            target=1000.0,
            comparator="lte",
            metric=DEPLOY_DURATION_MS,
        )
    )
    collector = DeploymentMetricsCollector()
    collector.observe(DEPLOY_DURATION_MS, 50.0)
    manager.evaluate(collector.snapshot(), timestamp=BASE_TIME)

    result = manager.status("latency")

    assert result.objective_name == "latency"
    assert manager.status() == [result]


def test_status_unknown_objective_raises(manager: DeploymentSLOManager):
    with pytest.raises(UnknownObjectiveError):
        manager.status("does-not-exist")


def test_default_objectives_are_registered_on_construction():
    manager = DeploymentSLOManager()

    names = {obj.name for obj in manager.list_objectives()}

    assert names == {
        "deployment_success_rate",
        "deployment_latency",
        "rollback_rate",
        "service_availability",
    }


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_slo_router)
    return TestClient(app)


def test_api_list_objectives(client: TestClient):
    response = client.get("/governance/slo")

    assert response.status_code == 200
    names = {obj["name"] for obj in response.json()}
    assert "deployment_success_rate" in names


def test_api_evaluate_and_status(client: TestClient):
    from backend.governance.deployment_metrics import (
        get_deployment_metrics_collector,
    )

    metrics_collector = get_deployment_metrics_collector()
    metrics_collector.clear()
    metrics_collector.observe(DEPLOY_DURATION_MS, 100.0)

    evaluate_response = client.post("/governance/slo/evaluate")
    status_response = client.get("/governance/slo/status")

    assert evaluate_response.status_code == 200
    assert status_response.status_code == 200
    assert any(
        r["objective_name"] == "deployment_latency"
        for r in status_response.json()
    )
