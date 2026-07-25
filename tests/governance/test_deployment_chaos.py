from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_chaos import (
    ChaosExperiment,
    DeploymentChaosFramework,
    DeploymentFailureExperiment,
    UnknownExecutionError,
    UnknownExperimentError,
    router as deployment_chaos_router,
)
from backend.governance.deployment_recovery import DeploymentRecoveryCoordinator

BASE_TIME = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


class _ManualTimerHandle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _ManualScheduler:
    """Test double: records the scheduled callback instead of waiting."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, callable, _ManualTimerHandle]] = []

    def __call__(self, delay, callback):
        handle = _ManualTimerHandle()
        self.calls.append((delay, callback, handle))
        return handle

    def fire_latest(self) -> None:
        _, callback, _ = self.calls[-1]
        callback()


class _RecordingExperiment(ChaosExperiment):
    def __init__(self, name: str = "recording") -> None:
        super().__init__(name)
        self.injected = []
        self.cleaned_up = []

    def inject(self, context):
        self.injected.append(dict(context))
        return f"injected fault for {context.get('deployment')}"

    def cleanup(self, context):
        self.cleaned_up.append(dict(context))
        return f"cleaned up fault for {context.get('deployment')}"


class _FailsToInjectExperiment(ChaosExperiment):
    def inject(self, context):
        raise RuntimeError("cannot inject fault")

    def cleanup(self, context):
        return "noop"


@pytest.fixture
def framework() -> DeploymentChaosFramework:
    return DeploymentChaosFramework(experiments=[])


def test_register_experiment_makes_it_runnable(framework: DeploymentChaosFramework):
    framework.register_experiment(_RecordingExperiment())
    scheduler = _ManualScheduler()

    record = framework.run(
        "recording",
        {"deployment": "svc-a"},
        timestamp=BASE_TIME,
        scheduler=scheduler,
    )

    assert record.status == "RUNNING"
    assert record.experiment == "recording"


def test_run_unknown_experiment_raises(framework: DeploymentChaosFramework):
    with pytest.raises(UnknownExperimentError):
        framework.run("does-not-exist", {"deployment": "svc-a"})


def test_run_requires_deployment_identifier(framework: DeploymentChaosFramework):
    framework.register_experiment(_RecordingExperiment())

    with pytest.raises(ValueError):
        framework.run("recording", {})


def test_run_injects_the_fault_immediately(framework: DeploymentChaosFramework):
    experiment = _RecordingExperiment()
    framework.register_experiment(experiment)
    scheduler = _ManualScheduler()

    framework.run(
        "recording", {"deployment": "svc-a"}, timestamp=BASE_TIME, scheduler=scheduler
    )

    assert len(experiment.injected) == 1
    assert experiment.injected[0]["deployment"] == "svc-a"
    assert experiment.cleaned_up == []


def test_automatic_cleanup_fires_after_scheduled_duration(
    framework: DeploymentChaosFramework,
):
    experiment = _RecordingExperiment()
    framework.register_experiment(experiment)
    scheduler = _ManualScheduler()

    record = framework.run(
        "recording",
        {"deployment": "svc-a"},
        duration_seconds=5.0,
        timestamp=BASE_TIME,
        scheduler=scheduler,
    )
    assert scheduler.calls[0][0] == 5.0

    scheduler.fire_latest()

    assert len(experiment.cleaned_up) == 1
    history = framework.history()
    assert len(history) == 1
    assert history[0].status == "COMPLETED"
    assert history[0].execution_id == record.execution_id


def test_stop_cancels_the_timer_and_cleans_up_immediately(
    framework: DeploymentChaosFramework,
):
    experiment = _RecordingExperiment()
    framework.register_experiment(experiment)
    scheduler = _ManualScheduler()

    record = framework.run(
        "recording",
        {"deployment": "svc-a"},
        duration_seconds=999.0,
        timestamp=BASE_TIME,
        scheduler=scheduler,
    )
    handle = scheduler.calls[0][2]

    stopped = framework.stop(record.execution_id)

    assert handle.cancelled is True
    assert stopped.status == "STOPPED"
    assert len(experiment.cleaned_up) == 1


def test_stop_unknown_execution_raises(framework: DeploymentChaosFramework):
    with pytest.raises(UnknownExecutionError):
        framework.stop("does-not-exist")


def test_run_records_failure_when_injection_raises(
    framework: DeploymentChaosFramework,
):
    framework.register_experiment(_FailsToInjectExperiment("bad"))

    record = framework.run("bad", {"deployment": "svc-a"}, timestamp=BASE_TIME)

    assert record.status == "FAILED"
    assert record.injected_message is None
    history = framework.history()
    assert len(history) == 1


def test_history_filters_by_deployment(framework: DeploymentChaosFramework):
    experiment = _RecordingExperiment()
    framework.register_experiment(experiment)
    scheduler = _ManualScheduler()

    framework.run(
        "recording", {"deployment": "svc-a"}, timestamp=BASE_TIME, scheduler=scheduler
    )
    scheduler.fire_latest()
    framework.run(
        "recording", {"deployment": "svc-b"}, timestamp=BASE_TIME, scheduler=scheduler
    )
    scheduler.fire_latest()

    assert len(framework.history()) == 2
    assert len(framework.history(deployment="svc-a")) == 1


def test_default_experiments_are_registered_on_construction():
    framework = DeploymentChaosFramework()

    names = {name for name in framework._experiments}

    assert names == {
        "deployment_failure",
        "network_latency",
        "service_timeout",
        "node_unavailable",
        "resource_exhaustion",
    }


def test_run_triggers_recovery_coordinator_when_provided(
    framework: DeploymentChaosFramework,
):
    framework.register_experiment(DeploymentFailureExperiment())
    recovery_coordinator = DeploymentRecoveryCoordinator()
    scheduler = _ManualScheduler()

    record = framework.run(
        "deployment_failure",
        {"deployment": "svc-a"},
        timestamp=BASE_TIME,
        scheduler=scheduler,
        recovery_coordinator=recovery_coordinator,
    )

    assert record.recovery_id is not None
    recovery_record = recovery_coordinator.status("svc-a")
    assert recovery_record.source == "chaos"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_chaos_router)
    return TestClient(app)


def test_api_run_and_history(client: TestClient):
    response = client.post(
        "/governance/chaos/run",
        json={
            "experiment": "deployment_failure",
            "deployment": "svc-api-1",
            "duration_seconds": 0.01,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "RUNNING"

    history_response = client.get(
        "/governance/chaos/history", params={"deployment": "svc-api-1"}
    )
    assert history_response.status_code == 200


def test_api_run_requires_experiment_and_deployment(client: TestClient):
    response = client.post("/governance/chaos/run", json={"deployment": "svc-a"})

    assert response.status_code == 422


def test_api_run_unknown_experiment_returns_404(client: TestClient):
    response = client.post(
        "/governance/chaos/run",
        json={"experiment": "does-not-exist", "deployment": "svc-a"},
    )

    assert response.status_code == 404


def test_api_stop(client: TestClient):
    run_response = client.post(
        "/governance/chaos/run",
        json={
            "experiment": "network_latency",
            "deployment": "svc-api-2",
            "duration_seconds": 999.0,
        },
    )
    execution_id = run_response.json()["execution_id"]

    stop_response = client.post(
        "/governance/chaos/stop", json={"execution_id": execution_id}
    )

    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "STOPPED"


def test_api_stop_unknown_execution_returns_404(client: TestClient):
    response = client.post(
        "/governance/chaos/stop", json={"execution_id": "does-not-exist"}
    )

    assert response.status_code == 404
