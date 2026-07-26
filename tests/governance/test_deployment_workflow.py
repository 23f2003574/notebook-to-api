from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_pipeline import (
    DeploymentPipelineEngine,
    PipelineStage,
    router as deployment_pipeline_router,
)
from backend.governance.deployment_workflow import (
    DeploymentWorkflowEngine,
    InvalidTransitionError,
    UnknownExecutionError,
    WorkflowExecution,
    router as deployment_workflow_router,
)

BASE_TIME = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine() -> DeploymentWorkflowEngine:
    return DeploymentWorkflowEngine()


def test_start_creates_running_execution(engine: DeploymentWorkflowEngine):
    execution = engine.start("svc-a", timestamp=BASE_TIME)

    assert execution.status == "RUNNING"
    assert execution.pipeline == "svc-a"
    assert len(execution.history) == 1


def test_start_requires_pipeline(engine: DeploymentWorkflowEngine):
    with pytest.raises(ValueError):
        engine.start("")


def test_start_uses_pipeline_engine_for_stages(engine: DeploymentWorkflowEngine):
    pipeline_engine = DeploymentPipelineEngine()
    pipeline_engine.register(
        "svc-a", [PipelineStage(name="build", action="build_image")]
    )

    execution = engine.start(
        "svc-a", pipeline_engine=pipeline_engine, timestamp=BASE_TIME
    )

    assert execution.stages == ("build",)


def test_pause_then_resume_transitions_status(engine: DeploymentWorkflowEngine):
    execution = engine.start("svc-a", timestamp=BASE_TIME)

    paused = engine.pause(execution.execution_id, timestamp=BASE_TIME)
    resumed = engine.resume(execution.execution_id, timestamp=BASE_TIME)

    assert paused.status == "PAUSED"
    assert resumed.status == "RUNNING"
    assert len(resumed.history) == 3


def test_cancel_from_running(engine: DeploymentWorkflowEngine):
    execution = engine.start("svc-a", timestamp=BASE_TIME)

    cancelled = engine.cancel(execution.execution_id, timestamp=BASE_TIME)

    assert cancelled.status == "CANCELLED"


def test_cancel_from_paused(engine: DeploymentWorkflowEngine):
    execution = engine.start("svc-a", timestamp=BASE_TIME)
    engine.pause(execution.execution_id, timestamp=BASE_TIME)

    cancelled = engine.cancel(execution.execution_id, timestamp=BASE_TIME)

    assert cancelled.status == "CANCELLED"


def test_pause_already_paused_raises(engine: DeploymentWorkflowEngine):
    execution = engine.start("svc-a", timestamp=BASE_TIME)
    engine.pause(execution.execution_id, timestamp=BASE_TIME)

    with pytest.raises(InvalidTransitionError):
        engine.pause(execution.execution_id, timestamp=BASE_TIME)


def test_resume_running_raises(engine: DeploymentWorkflowEngine):
    execution = engine.start("svc-a", timestamp=BASE_TIME)

    with pytest.raises(InvalidTransitionError):
        engine.resume(execution.execution_id, timestamp=BASE_TIME)


def test_transition_unknown_execution_raises(engine: DeploymentWorkflowEngine):
    with pytest.raises(UnknownExecutionError):
        engine.pause("does-not-exist")


def test_status_returns_execution(engine: DeploymentWorkflowEngine):
    execution = engine.start("svc-a", timestamp=BASE_TIME)

    result = engine.status(execution.execution_id)

    assert isinstance(result, WorkflowExecution)
    assert result.execution_id == execution.execution_id


def test_status_unknown_execution_raises(engine: DeploymentWorkflowEngine):
    with pytest.raises(UnknownExecutionError):
        engine.status("does-not-exist")


def test_on_transition_hook_invoked_for_each_change(engine: DeploymentWorkflowEngine):
    seen = []
    engine.on_transition(lambda execution: seen.append(execution.status))

    execution = engine.start("svc-a", timestamp=BASE_TIME)
    engine.pause(execution.execution_id, timestamp=BASE_TIME)

    assert seen == ["RUNNING", "PAUSED"]


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_pipeline_router)
    app.include_router(deployment_workflow_router)
    return TestClient(app)


def test_api_start_requires_registered_pipeline(client: TestClient):
    response = client.post("/governance/workflows/start", json={"pipeline": "svc-missing"})

    assert response.status_code == 404


def test_api_start_pause_resume_and_get(client: TestClient):
    client.post(
        "/governance/pipelines",
        json={
            "name": "svc-api-1",
            "stages": [{"name": "build", "action": "build_image"}],
        },
    )

    start_response = client.post(
        "/governance/workflows/start", json={"pipeline": "svc-api-1"}
    )
    execution_id = start_response.json()["execution_id"]

    pause_response = client.post(f"/governance/workflows/{execution_id}/pause")
    resume_response = client.post(f"/governance/workflows/{execution_id}/resume")
    get_response = client.get(f"/governance/workflows/{execution_id}")

    assert start_response.status_code == 200
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "PAUSED"
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "RUNNING"
    assert get_response.status_code == 200


def test_api_pause_unknown_execution_returns_404(client: TestClient):
    response = client.post("/governance/workflows/does-not-exist/pause")

    assert response.status_code == 404


def test_api_pause_twice_returns_409(client: TestClient):
    client.post(
        "/governance/pipelines",
        json={
            "name": "svc-api-2",
            "stages": [{"name": "build", "action": "build_image"}],
        },
    )
    start_response = client.post(
        "/governance/workflows/start", json={"pipeline": "svc-api-2"}
    )
    execution_id = start_response.json()["execution_id"]
    client.post(f"/governance/workflows/{execution_id}/pause")

    response = client.post(f"/governance/workflows/{execution_id}/pause")

    assert response.status_code == 409


def test_api_get_unknown_execution_returns_404(client: TestClient):
    response = client.get("/governance/workflows/does-not-exist")

    assert response.status_code == 404
