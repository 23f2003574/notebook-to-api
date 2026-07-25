from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_logging import (
    DeploymentLoggingService,
    router as deployment_logging_router,
)

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def service() -> DeploymentLoggingService:
    return DeploymentLoggingService()


def test_log_writes_structured_entry(service: DeploymentLoggingService):
    entry = service.log(
        "deploy-1",
        "info",
        "rollout started",
        context={"region": "us-east-1"},
        timestamp=BASE_TIME,
    )

    assert entry.deployment == "deploy-1"
    assert entry.level == "INFO"
    assert entry.message == "rollout started"
    assert entry.timestamp == BASE_TIME
    assert entry.context == {"region": "us-east-1"}


def test_log_defaults_to_utc_now(service: DeploymentLoggingService):
    entry = service.log("deploy-1", "DEBUG", "checking readiness")

    assert entry.timestamp.tzinfo == timezone.utc


def test_log_rejects_missing_deployment(service: DeploymentLoggingService):
    with pytest.raises(ValueError):
        service.log("", "INFO", "no correlation id")


def test_log_rejects_unknown_level(service: DeploymentLoggingService):
    with pytest.raises(ValueError):
        service.log("deploy-1", "TRACE", "bad level")


def test_query_filters_by_deployment(service: DeploymentLoggingService):
    service.log("deploy-1", "INFO", "a", timestamp=BASE_TIME)
    service.log("deploy-2", "INFO", "b", timestamp=BASE_TIME)

    results = service.query(deployment="deploy-1")

    assert [entry.message for entry in results] == ["a"]


def test_query_severity_filter_is_inclusive_of_higher_levels(
    service: DeploymentLoggingService,
):
    service.log("deploy-1", "DEBUG", "debug msg", timestamp=BASE_TIME)
    service.log("deploy-1", "WARNING", "warning msg", timestamp=BASE_TIME)
    service.log("deploy-1", "CRITICAL", "critical msg", timestamp=BASE_TIME)

    results = service.query(level="WARNING")

    assert [entry.level for entry in results] == ["WARNING", "CRITICAL"]


def test_export_returns_json_array_of_entries(service: DeploymentLoggingService):
    service.log("deploy-1", "ERROR", "boom", timestamp=BASE_TIME)

    exported = service.export(deployment="deploy-1")

    assert exported == service.export(deployment="deploy-1")
    entries = service.query(deployment="deploy-1")
    assert len(entries) == 1
    assert "boom" in exported


def test_clear_empties_the_log_store(service: DeploymentLoggingService):
    service.log("deploy-1", "INFO", "a", timestamp=BASE_TIME)

    service.clear()

    assert service.query() == []


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_logging_router)
    return TestClient(app)


def test_api_list_logs_for_deployment(client: TestClient):
    from backend.governance.deployment_logging import (
        get_deployment_logging_service,
    )

    get_deployment_logging_service().clear()
    get_deployment_logging_service().log(
        "deploy-1", "INFO", "hello", timestamp=BASE_TIME
    )

    response = client.get("/governance/logs/deploy-1")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["message"] == "hello"


def test_api_export_endpoint(client: TestClient):
    from backend.governance.deployment_logging import (
        get_deployment_logging_service,
    )

    get_deployment_logging_service().clear()
    get_deployment_logging_service().log(
        "deploy-2", "CRITICAL", "meltdown", timestamp=BASE_TIME
    )

    response = client.post(
        "/governance/logs/export", params={"deployment": "deploy-2"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["level"] == "CRITICAL"
