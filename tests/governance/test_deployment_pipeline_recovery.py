from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_approval_gate import (
    router as deployment_approval_gate_router,
)
from backend.governance.deployment_pipeline import (
    DeploymentPipelineEngine,
    PipelineStage,
    router as deployment_pipeline_router,
)
from backend.governance.deployment_pipeline_recovery import (
    DeploymentPipelineRecoveryManager,
    PipelineRecovery,
    router as deployment_pipeline_recovery_router,
)
from backend.governance.deployment_recovery import (
    DeploymentRecoveryCoordinator,
    router as deployment_recovery_router,
)
from backend.governance.deployment_stage_orchestrator import (
    DeploymentStageOrchestrator,
    router as deployment_stage_router,
)
from backend.governance.deployment_workflow import (
    DeploymentWorkflowEngine,
    router as deployment_workflow_router,
)

BASE_TIME = datetime(2026, 7, 26, 9, 0, 0, tzinfo=timezone.utc)


def _failed_execution():
    pipeline_engine = DeploymentPipelineEngine()
    pipeline_engine.register("svc-a", [PipelineStage(name="build", action="build")])
    wf_engine = DeploymentWorkflowEngine(pipeline_engine=pipeline_engine)
    execution = wf_engine.start("svc-a", timestamp=BASE_TIME)
    wf_engine.fail(execution.execution_id, timestamp=BASE_TIME)
    return wf_engine, execution.execution_id


@pytest.fixture
def manager() -> DeploymentPipelineRecoveryManager:
    return DeploymentPipelineRecoveryManager()


def test_resume_recovers_failed_execution_to_running(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()

    record = manager.resume(execution_id, workflow_engine=wf_engine, timestamp=BASE_TIME)

    assert isinstance(record, PipelineRecovery)
    assert record.action.status == "SUCCEEDED"
    assert wf_engine.status(execution_id).status == "RUNNING"


def test_resume_unknown_execution_raises(manager: DeploymentPipelineRecoveryManager):
    wf_engine = DeploymentWorkflowEngine()

    with pytest.raises(KeyError):
        manager.resume("does-not-exist", workflow_engine=wf_engine)


def test_resume_records_failed_action_for_invalid_transition(
    manager: DeploymentPipelineRecoveryManager,
):
    pipeline_engine = DeploymentPipelineEngine()
    pipeline_engine.register("svc-a", [PipelineStage(name="build", action="build")])
    wf_engine = DeploymentWorkflowEngine(pipeline_engine=pipeline_engine)
    execution = wf_engine.start("svc-a", timestamp=BASE_TIME)

    record = manager.resume(execution.execution_id, workflow_engine=wf_engine, timestamp=BASE_TIME)

    assert record.action.status == "FAILED"


def test_restart_stage_retries_failed_stage(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()
    wf_engine.recover(execution_id, timestamp=BASE_TIME)
    orchestrator = DeploymentStageOrchestrator(workflow_engine=wf_engine)
    orchestrator.execute_stage(
        execution_id, "build", action=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        workflow_engine=wf_engine, timestamp=BASE_TIME,
    )

    record = manager.restart(
        execution_id,
        scope="stage",
        stage="build",
        workflow_engine=wf_engine,
        stage_orchestrator=orchestrator,
        timestamp=BASE_TIME,
    )

    assert record.action.mode == "restart_stage"
    assert record.action.status == "SUCCEEDED"
    assert record.action.target == "build"


def test_restart_stage_requires_stage_name(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()

    with pytest.raises(ValueError):
        manager.restart(execution_id, scope="stage", workflow_engine=wf_engine)


def test_restart_stage_requires_orchestrator(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()

    with pytest.raises(ValueError):
        manager.restart(execution_id, scope="stage", stage="build", workflow_engine=wf_engine)


def test_restart_pipeline_starts_new_execution(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()

    record = manager.restart(
        execution_id, scope="pipeline", workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    assert record.action.mode == "restart_pipeline"
    assert record.action.status == "SUCCEEDED"
    assert record.action.target != execution_id
    assert wf_engine.status(record.action.target).status == "RUNNING"


def test_restart_unknown_scope_raises(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()

    with pytest.raises(ValueError):
        manager.restart(execution_id, scope="galaxy", workflow_engine=wf_engine)


def test_restart_unknown_execution_raises(manager: DeploymentPipelineRecoveryManager):
    wf_engine = DeploymentWorkflowEngine()

    with pytest.raises(KeyError):
        manager.restart("does-not-exist", workflow_engine=wf_engine)


def test_recover_dispatches_resume_mode(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()

    record = manager.recover(
        execution_id, "resume", workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    assert record.action.mode == "resume"


def test_recover_dispatches_restart_pipeline_mode(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()

    record = manager.recover(
        execution_id, "restart_pipeline", workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    assert record.action.mode == "restart_pipeline"


def test_recover_dispatches_restart_stage_mode(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()
    wf_engine.recover(execution_id, timestamp=BASE_TIME)
    orchestrator = DeploymentStageOrchestrator(workflow_engine=wf_engine)
    orchestrator.execute_stage(
        execution_id,
        "build",
        action=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        workflow_engine=wf_engine,
        timestamp=BASE_TIME,
    )

    record = manager.recover(
        execution_id,
        "restart_stage",
        stage="build",
        workflow_engine=wf_engine,
        stage_orchestrator=orchestrator,
        timestamp=BASE_TIME,
    )

    assert record.action.mode == "restart_stage"
    assert record.action.status == "SUCCEEDED"


def test_recover_rollback_delegates_to_recovery_coordinator(
    manager: DeploymentPipelineRecoveryManager,
):
    wf_engine, execution_id = _failed_execution()
    coordinator = DeploymentRecoveryCoordinator()

    record = manager.recover(
        execution_id,
        "rollback",
        workflow_engine=wf_engine,
        recovery_coordinator=coordinator,
        timestamp=BASE_TIME,
    )

    assert record.action.mode == "rollback"
    assert record.action.status == "SUCCEEDED"
    assert len(coordinator.history()) == 1


def test_recover_rollback_requires_coordinator(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()

    with pytest.raises(ValueError):
        manager.recover(execution_id, "rollback", workflow_engine=wf_engine)


def test_recover_unknown_mode_raises(manager: DeploymentPipelineRecoveryManager):
    wf_engine, execution_id = _failed_execution()

    with pytest.raises(ValueError):
        manager.recover(execution_id, "teleport", workflow_engine=wf_engine)


def test_history_returns_all_recovery_attempts_in_order(
    manager: DeploymentPipelineRecoveryManager,
):
    wf_engine, execution_id = _failed_execution()

    manager.resume(execution_id, workflow_engine=wf_engine, timestamp=BASE_TIME)
    manager.restart(execution_id, scope="pipeline", workflow_engine=wf_engine, timestamp=BASE_TIME)

    history = manager.history(execution_id)

    assert [record.action.mode for record in history] == ["resume", "restart_pipeline"]


def test_history_empty_for_unknown_execution(manager: DeploymentPipelineRecoveryManager):
    assert manager.history("does-not-exist") == ()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_pipeline_router)
    app.include_router(deployment_workflow_router)
    app.include_router(deployment_stage_router)
    app.include_router(deployment_recovery_router)
    app.include_router(deployment_approval_gate_router)
    app.include_router(deployment_pipeline_recovery_router)
    return TestClient(app)


def _start_and_fail(client: TestClient, pipeline_name: str) -> str:
    client.post(
        "/governance/pipelines",
        json={"name": pipeline_name, "stages": [{"name": "build", "action": "build"}]},
    )
    start_response = client.post("/governance/workflows/start", json={"pipeline": pipeline_name})
    execution_id = start_response.json()["execution_id"]
    gate_response = client.post(
        "/governance/approval-gates", json={"execution_id": execution_id, "stage": "build"}
    )
    gate_id = gate_response.json()["gate_id"]
    client.post(f"/governance/approval-gates/{gate_id}/reject", json={"approver": "alice"})
    return execution_id


def test_api_recover_resume_and_history(client: TestClient):
    execution_id = _start_and_fail(client, "svc-recovery-api-1")

    recover_response = client.post(
        f"/governance/pipeline-recovery/{execution_id}/recover", json={"mode": "resume"}
    )
    history_response = client.get(f"/governance/pipeline-recovery/{execution_id}/history")

    assert recover_response.status_code == 200
    assert recover_response.json()["action"]["mode"] == "resume"
    assert history_response.status_code == 200
    assert len(history_response.json()) == 1


def test_api_resume_endpoint(client: TestClient):
    execution_id = _start_and_fail(client, "svc-recovery-api-2")

    response = client.post(f"/governance/pipeline-recovery/{execution_id}/resume")

    assert response.status_code == 200
    assert response.json()["action"]["status"] == "SUCCEEDED"


def test_api_recover_restart_pipeline(client: TestClient):
    execution_id = _start_and_fail(client, "svc-recovery-api-3")

    response = client.post(
        f"/governance/pipeline-recovery/{execution_id}/recover",
        json={"mode": "restart_pipeline"},
    )

    assert response.status_code == 200
    assert response.json()["action"]["mode"] == "restart_pipeline"


def test_api_recover_rollback(client: TestClient):
    execution_id = _start_and_fail(client, "svc-recovery-api-4")

    response = client.post(
        f"/governance/pipeline-recovery/{execution_id}/recover", json={"mode": "rollback"}
    )

    assert response.status_code == 200
    assert response.json()["action"]["mode"] == "rollback"


def test_api_recover_requires_mode(client: TestClient):
    execution_id = _start_and_fail(client, "svc-recovery-api-5")

    response = client.post(f"/governance/pipeline-recovery/{execution_id}/recover", json={})

    assert response.status_code == 422


def test_api_recover_unknown_mode_returns_422(client: TestClient):
    execution_id = _start_and_fail(client, "svc-recovery-api-6")

    response = client.post(
        f"/governance/pipeline-recovery/{execution_id}/recover", json={"mode": "teleport"}
    )

    assert response.status_code == 422


def test_api_recover_unknown_execution_returns_404(client: TestClient):
    response = client.post(
        "/governance/pipeline-recovery/does-not-exist/recover", json={"mode": "resume"}
    )

    assert response.status_code == 404


def test_api_resume_unknown_execution_returns_404(client: TestClient):
    response = client.post("/governance/pipeline-recovery/does-not-exist/resume")

    assert response.status_code == 404


def test_api_history_empty_for_unknown_execution(client: TestClient):
    response = client.get("/governance/pipeline-recovery/does-not-exist/history")

    assert response.status_code == 200
    assert response.json() == []
