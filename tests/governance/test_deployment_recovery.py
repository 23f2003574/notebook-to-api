from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_alerts import DeploymentAlertManager
from backend.governance.deployment_metrics import DeploymentMetricsCollector
from backend.governance.deployment_recovery import (
    DeploymentRecoveryCoordinator,
    NoApplicableStrategyError,
    RecoveryStrategy,
    RollbackStrategy,
    UnknownDeploymentError,
    UnknownStrategyError,
    router as deployment_recovery_router,
)

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


class _AlwaysFailsStrategy(RecoveryStrategy):
    def __init__(self, name: str = "always_fails", *, priority: int = 5) -> None:
        super().__init__(name, priority=priority)
        self.calls = 0

    def execute(self, context):
        self.calls += 1
        raise RuntimeError(f"{self.name} could not recover")


class _UnavailableStrategy(RecoveryStrategy):
    def can_handle(self, context):
        return False

    def execute(self, context):
        raise AssertionError("should never be called")


class _AlwaysSucceedsStrategy(RecoveryStrategy):
    def execute(self, context):
        return f"{self.name} handled {context.get('deployment')}"


@pytest.fixture
def coordinator() -> DeploymentRecoveryCoordinator:
    return DeploymentRecoveryCoordinator(strategies=[])


def test_register_strategy_adds_it(coordinator: DeploymentRecoveryCoordinator):
    coordinator.register_strategy(RollbackStrategy())

    record = coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)

    assert record.strategy == "rollback"


def test_recover_selects_highest_priority_applicable_strategy(
    coordinator: DeploymentRecoveryCoordinator,
):
    coordinator.register_strategy(RollbackStrategy(priority=10))
    coordinator.register_strategy(
        _AlwaysSucceedsStrategy("preferred", priority=1)
    )

    record = coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)

    assert record.status == "SUCCEEDED"
    assert record.strategy == "preferred"


def test_recover_skips_strategies_that_cannot_handle_context(
    coordinator: DeploymentRecoveryCoordinator,
):
    coordinator.register_strategy(_UnavailableStrategy("unavailable", priority=1))
    coordinator.register_strategy(RollbackStrategy(priority=10))

    record = coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)

    assert record.strategy == "rollback"
    assert record.status == "SUCCEEDED"


def test_rollback_execution_produces_expected_message(
    coordinator: DeploymentRecoveryCoordinator,
):
    coordinator.register_strategy(RollbackStrategy())

    record = coordinator.recover(
        {"deployment": "svc-a", "has_previous_version": True}, timestamp=BASE_TIME
    )

    assert "rolled back" in record.message
    assert record.deployment == "svc-a"


def test_recover_falls_through_to_next_strategy_on_failure(
    coordinator: DeploymentRecoveryCoordinator,
):
    failing = _AlwaysFailsStrategy("first", priority=1)
    coordinator.register_strategy(failing)
    coordinator.register_strategy(RollbackStrategy(priority=10))

    record = coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)

    assert failing.calls == 1
    assert record.strategy == "rollback"
    assert record.status == "SUCCEEDED"


def test_recover_records_failure_when_all_strategies_fail(
    coordinator: DeploymentRecoveryCoordinator,
):
    coordinator.register_strategy(_AlwaysFailsStrategy("only", priority=1))

    record = coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)

    assert record.status == "FAILED"
    assert "could not recover" in record.message


def test_recover_with_no_candidates_raises(
    coordinator: DeploymentRecoveryCoordinator,
):
    coordinator.register_strategy(_UnavailableStrategy("unavailable"))

    with pytest.raises(NoApplicableStrategyError):
        coordinator.recover({"deployment": "svc-a"})


def test_recover_explicit_strategy_name_bypasses_selection(
    coordinator: DeploymentRecoveryCoordinator,
):
    coordinator.register_strategy(_UnavailableStrategy("unavailable"))
    coordinator.register_strategy(RollbackStrategy())

    record = coordinator.recover(
        {"deployment": "svc-a"}, strategy_name="rollback", timestamp=BASE_TIME
    )

    assert record.strategy == "rollback"


def test_recover_unknown_strategy_name_raises(
    coordinator: DeploymentRecoveryCoordinator,
):
    with pytest.raises(UnknownStrategyError):
        coordinator.recover({"deployment": "svc-a"}, strategy_name="nonexistent")


def test_recover_requires_deployment_identifier(
    coordinator: DeploymentRecoveryCoordinator,
):
    coordinator.register_strategy(RollbackStrategy())

    with pytest.raises(ValueError):
        coordinator.recover({})


def test_status_returns_latest_record_for_deployment(
    coordinator: DeploymentRecoveryCoordinator,
):
    coordinator.register_strategy(RollbackStrategy())
    coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)

    record = coordinator.status("svc-a")

    assert record.deployment == "svc-a"
    assert coordinator.status() == [record]


def test_status_unknown_deployment_raises(
    coordinator: DeploymentRecoveryCoordinator,
):
    with pytest.raises(UnknownDeploymentError):
        coordinator.status("does-not-exist")


def test_history_records_every_recovery_attempt(
    coordinator: DeploymentRecoveryCoordinator,
):
    coordinator.register_strategy(RollbackStrategy())

    coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)
    coordinator.recover({"deployment": "svc-a"}, timestamp=BASE_TIME)
    coordinator.recover({"deployment": "svc-b"}, timestamp=BASE_TIME)

    assert len(coordinator.history()) == 3
    assert len(coordinator.history(deployment="svc-a")) == 2


def test_default_strategies_are_registered_on_construction():
    coordinator = DeploymentRecoveryCoordinator()

    names = {s for s in coordinator._strategies}

    assert names == {
        "rollback",
        "retry_deployment",
        "pause_rollout",
        "manual_intervention",
    }


def test_alert_manager_evaluate_triggers_recovery():
    from backend.governance.deployment_alerts import AlertRule
    from backend.governance.deployment_metrics import FAILURE_COUNT

    manager = DeploymentAlertManager(rules=[])
    manager.register_rule(
        AlertRule(
            name="deployment_failure",
            level="ERROR",
            threshold=1,
            comparator="gte",
            metric=FAILURE_COUNT,
        )
    )
    collector = DeploymentMetricsCollector()
    collector.increment(FAILURE_COUNT, amount=1)
    recovery_coordinator = DeploymentRecoveryCoordinator()

    manager.evaluate(
        collector.snapshot(),
        timestamp=BASE_TIME,
        recovery_coordinator=recovery_coordinator,
    )

    record = recovery_coordinator.status("deployment_failure")
    assert record.status == "SUCCEEDED"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_recovery_router)
    return TestClient(app)


def test_api_start_recovery(client: TestClient):
    response = client.post(
        "/governance/recovery/start", json={"deployment": "svc-api-1"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deployment"] == "svc-api-1"
    assert body["status"] == "SUCCEEDED"


def test_api_start_recovery_requires_deployment(client: TestClient):
    response = client.post("/governance/recovery/start", json={})

    assert response.status_code == 422


def test_api_status_and_history(client: TestClient):
    client.post("/governance/recovery/start", json={"deployment": "svc-api-2"})

    status_response = client.get(
        "/governance/recovery/status", params={"deployment": "svc-api-2"}
    )
    history_response = client.get(
        "/governance/recovery/history", params={"deployment": "svc-api-2"}
    )

    assert status_response.status_code == 200
    assert status_response.json()["deployment"] == "svc-api-2"
    assert history_response.status_code == 200
    assert len(history_response.json()) >= 1


def test_api_status_unknown_deployment_returns_404(client: TestClient):
    response = client.get(
        "/governance/recovery/status", params={"deployment": "does-not-exist"}
    )

    assert response.status_code == 404
