from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_pipeline import (
    DeploymentPipelineEngine,
    PipelineStage,
    router as deployment_pipeline_router,
)
from backend.governance.deployment_stage_orchestrator import (
    DeploymentStageOrchestrator,
    InvalidRetryError,
    OutOfSequenceError,
    RetryLimitExceededError,
    StageAlreadyExecutedError,
    StageExecution,
    StageResult,
    UnknownStageError,
    router as deployment_stage_router,
)
from backend.governance.deployment_workflow import (
    DeploymentWorkflowEngine,
    router as deployment_workflow_router,
)

BASE_TIME = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)


def _wf_engine_with_execution(stages=("build", "deploy")):
    pipeline_engine = DeploymentPipelineEngine()
    pipeline_engine.register(
        "svc-a", [PipelineStage(name=name, action=name) for name in stages]
    )
    wf_engine = DeploymentWorkflowEngine(pipeline_engine=pipeline_engine)
    execution = wf_engine.start("svc-a", timestamp=BASE_TIME)
    return wf_engine, execution.execution_id


@pytest.fixture
def wf_setup():
    return _wf_engine_with_execution()


@pytest.fixture
def orchestrator() -> DeploymentStageOrchestrator:
    return DeploymentStageOrchestrator()


def test_execute_stage_runs_first_stage_in_sequence(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup

    result = orchestrator.execute_stage(
        execution_id, "build", workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    assert isinstance(result, StageResult)
    assert result.status == "SUCCEEDED"
    assert result.attempt == 1


def test_execute_stage_out_of_sequence_raises(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup

    with pytest.raises(OutOfSequenceError):
        orchestrator.execute_stage(
            execution_id, "deploy", workflow_engine=wf_engine, timestamp=BASE_TIME
        )


def test_execute_stage_unknown_stage_raises(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup

    with pytest.raises(UnknownStageError):
        orchestrator.execute_stage(
            execution_id, "does-not-exist", workflow_engine=wf_engine, timestamp=BASE_TIME
        )


def test_execute_stage_twice_raises(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup
    orchestrator.execute_stage(execution_id, "build", workflow_engine=wf_engine, timestamp=BASE_TIME)

    with pytest.raises(StageAlreadyExecutedError):
        orchestrator.execute_stage(execution_id, "build", workflow_engine=wf_engine, timestamp=BASE_TIME)


def test_next_stage_advances_after_success(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup

    assert orchestrator.next_stage(execution_id, workflow_engine=wf_engine) == "build"
    orchestrator.execute_stage(execution_id, "build", workflow_engine=wf_engine, timestamp=BASE_TIME)
    assert orchestrator.next_stage(execution_id, workflow_engine=wf_engine) == "deploy"


def test_completing_all_stages_completes_workflow(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup

    orchestrator.execute_stage(execution_id, "build", workflow_engine=wf_engine, timestamp=BASE_TIME)
    orchestrator.execute_stage(execution_id, "deploy", workflow_engine=wf_engine, timestamp=BASE_TIME)

    assert orchestrator.next_stage(execution_id, workflow_engine=wf_engine) is None
    assert wf_engine.status(execution_id).status == "COMPLETED"


def test_retry_flow_succeeds_after_failure(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup
    calls = {"n": 0}

    def action():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return "recovered"

    failed = orchestrator.execute_stage(
        execution_id, "build", action=action, workflow_engine=wf_engine, timestamp=BASE_TIME
    )
    retried = orchestrator.retry_stage(
        execution_id, "build", action=action, workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    assert failed.status == "FAILED"
    assert retried.status == "SUCCEEDED"
    assert retried.attempt == 2


def test_retry_non_failed_stage_raises(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup
    orchestrator.execute_stage(execution_id, "build", workflow_engine=wf_engine, timestamp=BASE_TIME)

    with pytest.raises(InvalidRetryError):
        orchestrator.retry_stage(execution_id, "build", workflow_engine=wf_engine, timestamp=BASE_TIME)


def test_retry_never_executed_stage_raises(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup

    with pytest.raises(InvalidRetryError):
        orchestrator.retry_stage(execution_id, "build", workflow_engine=wf_engine, timestamp=BASE_TIME)


def test_retry_limit_exceeded_fails_workflow(wf_setup):
    wf_engine, execution_id = wf_setup
    orchestrator = DeploymentStageOrchestrator(max_retries=2)

    def always_fails():
        raise RuntimeError("boom")

    orchestrator.execute_stage(
        execution_id, "build", action=always_fails, workflow_engine=wf_engine, timestamp=BASE_TIME
    )
    orchestrator.retry_stage(
        execution_id, "build", action=always_fails, workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    with pytest.raises(RetryLimitExceededError):
        orchestrator.retry_stage(
            execution_id, "build", action=always_fails, workflow_engine=wf_engine, timestamp=BASE_TIME
        )

    assert wf_engine.status(execution_id).status == "FAILED"


def test_timeout_handling_marks_stage_timed_out(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup
    clock_values = iter([0.0, 100.0])

    result = orchestrator.execute_stage(
        execution_id,
        "build",
        timeout_seconds=1.0,
        clock=lambda: next(clock_values),
        workflow_engine=wf_engine,
        timestamp=BASE_TIME,
    )

    assert result.status == "TIMED_OUT"


def test_skip_stage_marks_skipped_and_advances(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup

    result = orchestrator.skip_stage(
        execution_id, "build", reason="not needed", workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    assert result.status == "SKIPPED"
    assert orchestrator.next_stage(execution_id, workflow_engine=wf_engine) == "deploy"


def test_get_stage_reports_cumulative_execution(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup
    calls = {"n": 0}

    def action():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return "ok"

    orchestrator.execute_stage(
        execution_id, "build", action=action, workflow_engine=wf_engine, timestamp=BASE_TIME
    )
    orchestrator.retry_stage(
        execution_id, "build", action=action, workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    execution = orchestrator.get_stage("build")

    assert isinstance(execution, StageExecution)
    assert execution.status == "SUCCEEDED"
    assert execution.attempts == 2
    assert len(execution.results) == 2


def test_get_stage_unknown_raises(orchestrator):
    with pytest.raises(UnknownStageError):
        orchestrator.get_stage("does-not-exist")


def test_on_event_hook_invoked_for_each_attempt(orchestrator, wf_setup):
    wf_engine, execution_id = wf_setup
    seen = []
    orchestrator.on_event(lambda result: seen.append(result.status))

    orchestrator.execute_stage(execution_id, "build", workflow_engine=wf_engine, timestamp=BASE_TIME)

    assert seen == ["SUCCEEDED"]


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_pipeline_router)
    app.include_router(deployment_workflow_router)
    app.include_router(deployment_stage_router)
    return TestClient(app)


def _start_execution(client: TestClient, pipeline_name: str) -> str:
    client.post(
        "/governance/pipelines",
        json={
            "name": pipeline_name,
            "stages": [{"name": "build", "action": "build"}, {"name": "deploy", "action": "deploy"}],
        },
    )
    start_response = client.post("/governance/workflows/start", json={"pipeline": pipeline_name})
    return start_response.json()["execution_id"]


def test_api_execute_and_get_stage(client: TestClient):
    execution_id = _start_execution(client, "svc-api-1")

    execute_response = client.post(
        "/governance/stages/build/execute", json={"execution_id": execution_id}
    )
    get_response = client.get("/governance/stages/build")

    assert execute_response.status_code == 200
    assert execute_response.json()["status"] == "SUCCEEDED"
    assert get_response.status_code == 200
    assert get_response.json()["attempts"] == 1


def test_api_execute_requires_execution_id(client: TestClient):
    response = client.post("/governance/stages/build/execute", json={})

    assert response.status_code == 422


def test_api_execute_out_of_sequence_returns_409(client: TestClient):
    execution_id = _start_execution(client, "svc-api-2")

    response = client.post(
        "/governance/stages/deploy/execute", json={"execution_id": execution_id}
    )

    assert response.status_code == 409


def test_api_execute_forced_failure_then_retry(client: TestClient):
    execution_id = _start_execution(client, "svc-api-3")

    fail_response = client.post(
        "/governance/stages/build/execute",
        json={"execution_id": execution_id, "fail": True},
    )
    retry_response = client.post(
        "/governance/stages/build/retry", json={"execution_id": execution_id}
    )

    assert fail_response.json()["status"] == "FAILED"
    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "SUCCEEDED"


def test_api_execute_timeout_via_duration(client: TestClient):
    execution_id = _start_execution(client, "svc-api-4")

    response = client.post(
        "/governance/stages/build/execute",
        json={"execution_id": execution_id, "duration_seconds": 0.05, "timeout_seconds": 0.01},
    )

    assert response.json()["status"] == "TIMED_OUT"


def test_api_get_unknown_stage_returns_404(client: TestClient):
    response = client.get("/governance/stages/does-not-exist")

    assert response.status_code == 404
