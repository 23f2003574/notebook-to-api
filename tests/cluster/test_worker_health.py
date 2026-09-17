from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cluster.worker_discovery import WorkerDiscoveryService
from backend.cluster.worker_health import (
    DEGRADED,
    HEALTHY,
    HealthReport,
    UNHEALTHY,
    WorkerHealthManager,
    get_worker_health_manager,
    router as worker_health_router,
)
from backend.cluster.worker_registry import WorkerMetadata, WorkerRegistry


def make_metadata(hostname: str = "node-a.local") -> WorkerMetadata:
    return WorkerMetadata(hostname=hostname, region="us-east-1", version="1.0.0")


@pytest.fixture
def registry() -> WorkerRegistry:
    return WorkerRegistry()


@pytest.fixture
def discovery(registry: WorkerRegistry) -> WorkerDiscoveryService:
    return WorkerDiscoveryService(registry, stale_after_seconds=300.0)


@pytest.fixture
def manager(registry: WorkerRegistry, discovery: WorkerDiscoveryService) -> WorkerHealthManager:
    return WorkerHealthManager(registry, discovery, heartbeat_timeout_seconds=30.0)


@pytest.fixture
def client(manager: WorkerHealthManager) -> TestClient:
    app = FastAPI()
    app.include_router(worker_health_router)
    app.dependency_overrides[get_worker_health_manager] = lambda: manager
    return TestClient(app)


def test_check_healthy_worker(registry: WorkerRegistry, manager: WorkerHealthManager):
    registry.register("worker-1", ["parse"], make_metadata())

    report = manager.check("worker-1", metrics={"cpu_percent": 10.0, "memory_percent": 20.0})

    assert isinstance(report, HealthReport)
    assert report.status == HEALTHY
    assert all(check.passed for check in report.checks)


def test_check_degraded_worker_on_warn_threshold(registry: WorkerRegistry, manager: WorkerHealthManager):
    registry.register("worker-1", ["parse"], make_metadata())

    report = manager.check("worker-1", metrics={"cpu_percent": 85.0})

    assert report.status == DEGRADED


def test_check_unhealthy_worker_on_fail_threshold(registry: WorkerRegistry, manager: WorkerHealthManager):
    registry.register("worker-1", ["parse"], make_metadata())

    report = manager.check("worker-1", metrics={"memory_percent": 99.0})

    assert report.status == UNHEALTHY


def test_check_unhealthy_when_heartbeat_stale(registry: WorkerRegistry, manager: WorkerHealthManager):
    node = registry.register("worker-1", ["parse"], make_metadata())
    node.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=60)

    report = manager.check("worker-1")

    heartbeat_check = next(check for check in report.checks if check.check == "heartbeat")
    assert heartbeat_check.passed is False
    assert report.status == UNHEALTHY


def test_check_missing_worker_raises(manager: WorkerHealthManager):
    with pytest.raises(KeyError):
        manager.check("does-not-exist")


def test_check_marks_discovery_health(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, manager: WorkerHealthManager
):
    registry.register("worker-1", ["parse"], make_metadata())

    manager.check("worker-1", metrics={"disk_percent": 99.0})

    assert discovery.get_health("worker-1") == UNHEALTHY


def test_unhealthy_worker_excluded_from_available_workers(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, manager: WorkerHealthManager
):
    registry.register("worker-1", ["parse"], make_metadata())
    manager.check("worker-1", metrics={"disk_percent": 99.0})

    assert discovery.available_workers(capability="parse") == []


def test_heartbeat_updates_last_seen_and_returns_report(
    registry: WorkerRegistry, manager: WorkerHealthManager
):
    node = registry.register("worker-1", ["parse"], make_metadata())
    node.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=60)

    report = manager.heartbeat("worker-1", metrics={"cpu_percent": 5.0})

    assert report.status == HEALTHY
    heartbeat_check = next(check for check in report.checks if check.check == "heartbeat")
    assert heartbeat_check.passed is True


def test_heartbeat_missing_worker_raises(manager: WorkerHealthManager):
    with pytest.raises(KeyError):
        manager.heartbeat("does-not-exist")


def test_mark_unhealthy_sets_status_and_discovery_health(
    registry: WorkerRegistry, discovery: WorkerDiscoveryService, manager: WorkerHealthManager
):
    registry.register("worker-1", ["parse"], make_metadata())

    report = manager.mark_unhealthy("worker-1", reason="manual isolation")

    assert report.status == UNHEALTHY
    assert discovery.get_health("worker-1") == UNHEALTHY


def test_mark_unhealthy_missing_worker_raises(manager: WorkerHealthManager):
    with pytest.raises(KeyError):
        manager.mark_unhealthy("does-not-exist")


def test_recover_after_mark_unhealthy_with_good_metrics(registry: WorkerRegistry, manager: WorkerHealthManager):
    registry.register("worker-1", ["parse"], make_metadata())
    manager.mark_unhealthy("worker-1")

    report = manager.recover("worker-1", metrics={"cpu_percent": 5.0})

    assert report.status == HEALTHY


def test_recover_without_prior_unhealthy_mark_raises(registry: WorkerRegistry, manager: WorkerHealthManager):
    registry.register("worker-1", ["parse"], make_metadata())

    with pytest.raises(ValueError):
        manager.recover("worker-1")


def test_recover_missing_worker_raises(manager: WorkerHealthManager):
    with pytest.raises(KeyError):
        manager.recover("does-not-exist")


def test_list_reports_filters_by_status(registry: WorkerRegistry, manager: WorkerHealthManager):
    registry.register("worker-1", ["parse"], make_metadata())
    registry.register("worker-2", ["parse"], make_metadata())
    manager.check("worker-1", metrics={"cpu_percent": 5.0})
    manager.mark_unhealthy("worker-2")

    healthy = manager.list_reports(status=HEALTHY)
    unhealthy = manager.list_reports(status=UNHEALTHY)

    assert [report.worker_id for report in healthy] == ["worker-1"]
    assert [report.worker_id for report in unhealthy] == ["worker-2"]


def test_api_list_health(client: TestClient, registry: WorkerRegistry, manager: WorkerHealthManager):
    registry.register("worker-1", ["parse"], make_metadata())
    manager.check("worker-1")

    response = client.get("/cluster/health")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_get_health_runs_check_if_none_exists(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.get("/cluster/health/worker-1")

    assert response.status_code == 200
    assert response.json()["status"] == HEALTHY


def test_api_get_health_not_found(client: TestClient):
    response = client.get("/cluster/health/does-not-exist")

    assert response.status_code == 404


def test_api_heartbeat(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.post("/cluster/health/worker-1/heartbeat", json={"metrics": {"cpu_percent": 10.0}})

    assert response.status_code == 200
    assert response.json()["status"] == HEALTHY


def test_api_heartbeat_not_found(client: TestClient):
    response = client.post("/cluster/health/does-not-exist/heartbeat", json={})

    assert response.status_code == 404


def test_api_recover(client: TestClient, registry: WorkerRegistry, manager: WorkerHealthManager):
    registry.register("worker-1", ["parse"], make_metadata())
    manager.mark_unhealthy("worker-1")

    response = client.post("/cluster/health/worker-1/recover", json={"metrics": {"cpu_percent": 5.0}})

    assert response.status_code == 200
    assert response.json()["status"] == HEALTHY


def test_api_recover_not_unhealthy_returns_409(client: TestClient, registry: WorkerRegistry):
    registry.register("worker-1", ["parse"], make_metadata())

    response = client.post("/cluster/health/worker-1/recover", json={})

    assert response.status_code == 409


def test_api_recover_not_found(client: TestClient):
    response = client.post("/cluster/health/does-not-exist/recover", json={})

    assert response.status_code == 404
