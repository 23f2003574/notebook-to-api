from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_capacity import (
    DeploymentCapacityMonitor,
    ResourceDefinition,
    UnknownResourceError,
    router as deployment_capacity_router,
)
from backend.governance.deployment_metrics import (
    DeploymentMetricsCollector,
    capacity_metric_name,
)

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def monitor() -> DeploymentCapacityMonitor:
    return DeploymentCapacityMonitor(resources=[])


def test_register_resource_makes_it_available(monitor: DeploymentCapacityMonitor):
    monitor.register_resource(ResourceDefinition(name="cpu", capacity=100.0))

    assert [r.name for r in monitor.capacity()] == ["cpu"]


def test_register_resource_rejects_non_positive_capacity():
    with pytest.raises(ValueError):
        ResourceDefinition(name="cpu", capacity=0)


def test_register_resource_rejects_invalid_threshold_ordering():
    with pytest.raises(ValueError):
        ResourceDefinition(
            name="cpu", capacity=100.0, warning_threshold=0.9, critical_threshold=0.5
        )


def test_collect_unknown_resource_raises(monitor: DeploymentCapacityMonitor):
    with pytest.raises(UnknownResourceError):
        monitor.collect("does-not-exist", 10.0)


def test_collect_rejects_negative_usage(monitor: DeploymentCapacityMonitor):
    monitor.register_resource(ResourceDefinition(name="cpu", capacity=100.0))

    with pytest.raises(ValueError):
        monitor.collect("cpu", -1.0)


def test_utilization_calculation(monitor: DeploymentCapacityMonitor):
    monitor.register_resource(ResourceDefinition(name="cpu", capacity=200.0))

    measurement = monitor.collect("cpu", 50.0, timestamp=BASE_TIME)

    assert measurement.utilization == 0.25
    assert measurement.status == "OK"


def test_threshold_detection_warning_and_critical(
    monitor: DeploymentCapacityMonitor,
):
    monitor.register_resource(
        ResourceDefinition(
            name="cpu", capacity=100.0, warning_threshold=0.7, critical_threshold=0.9
        )
    )

    ok = monitor.collect("cpu", 50.0, timestamp=BASE_TIME)
    warning = monitor.collect("cpu", 75.0, timestamp=BASE_TIME)
    critical = monitor.collect("cpu", 95.0, timestamp=BASE_TIME)

    assert ok.status == "OK"
    assert warning.status == "WARNING"
    assert critical.status == "CRITICAL"


def test_utilization_returns_latest_measurement(monitor: DeploymentCapacityMonitor):
    monitor.register_resource(ResourceDefinition(name="cpu", capacity=100.0))
    monitor.collect("cpu", 10.0, timestamp=BASE_TIME)
    monitor.collect("cpu", 20.0, timestamp=BASE_TIME)

    latest = monitor.utilization("cpu")

    assert latest.used == 20.0
    assert len(monitor._history["cpu"]) == 2


def test_utilization_returns_none_when_no_measurements_yet(
    monitor: DeploymentCapacityMonitor,
):
    monitor.register_resource(ResourceDefinition(name="cpu", capacity=100.0))

    assert monitor.utilization("cpu") is None


def test_utilization_unknown_resource_raises(monitor: DeploymentCapacityMonitor):
    with pytest.raises(UnknownResourceError):
        monitor.utilization("does-not-exist")


def test_utilization_without_name_returns_all_latest(
    monitor: DeploymentCapacityMonitor,
):
    monitor.register_resource(ResourceDefinition(name="cpu", capacity=100.0))
    monitor.register_resource(ResourceDefinition(name="memory", capacity=100.0))
    monitor.collect("cpu", 10.0, timestamp=BASE_TIME)

    result = monitor.utilization()

    assert set(result) == {"cpu"}


def test_capacity_unknown_resource_raises(monitor: DeploymentCapacityMonitor):
    with pytest.raises(UnknownResourceError):
        monitor.capacity("does-not-exist")


def test_collect_publishes_gauge_to_metrics_collector(
    monitor: DeploymentCapacityMonitor,
):
    monitor.register_resource(ResourceDefinition(name="cpu", capacity=100.0))
    metrics_collector = DeploymentMetricsCollector()

    monitor.collect(
        "cpu", 40.0, timestamp=BASE_TIME, metrics_collector=metrics_collector
    )

    snapshot = metrics_collector.snapshot()
    assert snapshot.gauges[capacity_metric_name("cpu")] == 0.4


def test_default_resources_are_registered_on_construction():
    monitor = DeploymentCapacityMonitor()

    names = {r.name for r in monitor.capacity()}

    assert names == {"cpu", "memory", "disk", "network", "worker_pool"}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_capacity_router)
    return TestClient(app)


def test_api_list_capacity(client: TestClient):
    response = client.get("/governance/capacity")

    assert response.status_code == 200
    names = {r["name"] for r in response.json()}
    assert "cpu" in names


def test_api_collect_and_get_utilization(client: TestClient):
    collect_response = client.post(
        "/governance/capacity/collect", json={"resource": "cpu", "used": 60.0}
    )
    utilization_response = client.get(
        "/governance/capacity/utilization", params={"resource": "cpu"}
    )

    assert collect_response.status_code == 200
    assert collect_response.json()["used"] == 60.0
    assert utilization_response.status_code == 200
    assert utilization_response.json()["used"] == 60.0


def test_api_collect_requires_resource_and_used(client: TestClient):
    response = client.post("/governance/capacity/collect", json={"resource": "cpu"})

    assert response.status_code == 422


def test_api_collect_unknown_resource_returns_404(client: TestClient):
    response = client.post(
        "/governance/capacity/collect",
        json={"resource": "does-not-exist", "used": 1.0},
    )

    assert response.status_code == 404


def test_api_utilization_without_resource_returns_all(client: TestClient):
    client.post("/governance/capacity/collect", json={"resource": "memory", "used": 30.0})

    response = client.get("/governance/capacity/utilization")

    assert response.status_code == 200
    assert "memory" in response.json()
