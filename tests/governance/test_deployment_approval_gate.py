from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_approval_gate import (
    ApprovalGate,
    DeploymentApprovalGate,
    DuplicateDecisionError,
    InvalidGateStateError,
    UnauthorizedApproverError,
    UnknownGateError,
    router as deployment_approval_gate_router,
)
from backend.governance.deployment_pipeline import (
    DeploymentPipelineEngine,
    PipelineStage,
    router as deployment_pipeline_router,
)
from backend.governance.deployment_workflow import (
    DeploymentWorkflowEngine,
    router as deployment_workflow_router,
)

BASE_TIME = datetime(2026, 7, 26, 9, 0, 0, tzinfo=timezone.utc)


def _running_execution():
    pipeline_engine = DeploymentPipelineEngine()
    pipeline_engine.register("svc-a", [PipelineStage(name="deploy", action="deploy")])
    wf_engine = DeploymentWorkflowEngine(pipeline_engine=pipeline_engine)
    execution = wf_engine.start("svc-a", timestamp=BASE_TIME)
    return wf_engine, execution.execution_id


@pytest.fixture
def gate_service() -> DeploymentApprovalGate:
    return DeploymentApprovalGate()


def test_attach_creates_pending_gate(gate_service: DeploymentApprovalGate):
    gate = gate_service.attach("exec-1", "deploy", timestamp=BASE_TIME)

    assert isinstance(gate, ApprovalGate)
    assert gate.status == "PENDING"
    assert gate.required_approvals == 1


def test_attach_requires_execution_id(gate_service: DeploymentApprovalGate):
    with pytest.raises(ValueError):
        gate_service.attach("", "deploy")


def test_attach_requires_stage(gate_service: DeploymentApprovalGate):
    with pytest.raises(ValueError):
        gate_service.attach("exec-1", "")


def test_attach_requires_positive_required_approvals(gate_service: DeploymentApprovalGate):
    with pytest.raises(ValueError):
        gate_service.attach("exec-1", "deploy", required_approvals=0)


def test_attach_pauses_running_workflow(gate_service: DeploymentApprovalGate):
    wf_engine, execution_id = _running_execution()

    gate_service.attach(execution_id, "deploy", workflow_engine=wf_engine, timestamp=BASE_TIME)

    assert wf_engine.status(execution_id).status == "PAUSED"


def test_attach_timeout_sets_expires_at(gate_service: DeploymentApprovalGate):
    gate = gate_service.attach(
        "exec-1", "deploy", timeout_seconds=3600, timestamp=BASE_TIME
    )

    assert gate.expires_at == BASE_TIME + timedelta(seconds=3600)


def test_evaluate_returns_unchanged_before_expiry(gate_service: DeploymentApprovalGate):
    gate = gate_service.attach(
        "exec-1", "deploy", timeout_seconds=3600, timestamp=BASE_TIME
    )

    result = gate_service.evaluate(
        gate.gate_id, timestamp=BASE_TIME + timedelta(minutes=30)
    )

    assert result.status == "PENDING"


def test_evaluate_expires_gate_after_timeout(gate_service: DeploymentApprovalGate):
    wf_engine, execution_id = _running_execution()
    gate = gate_service.attach(
        execution_id,
        "deploy",
        timeout_seconds=60,
        workflow_engine=wf_engine,
        timestamp=BASE_TIME,
    )

    result = gate_service.evaluate(
        gate.gate_id, workflow_engine=wf_engine, timestamp=BASE_TIME + timedelta(minutes=2)
    )

    assert result.status == "EXPIRED"
    assert wf_engine.status(execution_id).status == "FAILED"


def test_approve_single_approver_opens_gate_and_resumes_workflow(
    gate_service: DeploymentApprovalGate,
):
    wf_engine, execution_id = _running_execution()
    gate = gate_service.attach(execution_id, "deploy", workflow_engine=wf_engine, timestamp=BASE_TIME)

    updated = gate_service.approve(
        gate.gate_id, "alice", workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    assert updated.status == "APPROVED"
    assert wf_engine.status(execution_id).status == "RUNNING"


def test_approve_multi_approver_requires_all_votes(gate_service: DeploymentApprovalGate):
    gate = gate_service.attach(
        "exec-1",
        "deploy",
        required_approvals=2,
        approvers=("alice", "bob"),
        timestamp=BASE_TIME,
    )

    once = gate_service.approve(gate.gate_id, "alice", timestamp=BASE_TIME)
    twice = gate_service.approve(gate.gate_id, "bob", timestamp=BASE_TIME)

    assert once.status == "PENDING"
    assert twice.status == "APPROVED"


def test_approve_unauthorized_approver_raises(gate_service: DeploymentApprovalGate):
    gate = gate_service.attach(
        "exec-1", "deploy", approvers=("alice",), timestamp=BASE_TIME
    )

    with pytest.raises(UnauthorizedApproverError):
        gate_service.approve(gate.gate_id, "mallory", timestamp=BASE_TIME)


def test_approve_duplicate_decision_raises(gate_service: DeploymentApprovalGate):
    gate = gate_service.attach(
        "exec-1",
        "deploy",
        required_approvals=2,
        approvers=("alice", "bob"),
        timestamp=BASE_TIME,
    )
    gate_service.approve(gate.gate_id, "alice", timestamp=BASE_TIME)

    with pytest.raises(DuplicateDecisionError):
        gate_service.approve(gate.gate_id, "alice", timestamp=BASE_TIME)


def test_approve_via_delegate(gate_service: DeploymentApprovalGate):
    gate = gate_service.attach(
        "exec-1",
        "deploy",
        approvers=("alice",),
        delegates={"alice": "carol"},
        timestamp=BASE_TIME,
    )

    updated = gate_service.approve(gate.gate_id, "carol", timestamp=BASE_TIME)

    assert updated.status == "APPROVED"
    assert updated.decisions[0].approver == "alice"


def test_approve_expired_gate_raises_invalid_state(gate_service: DeploymentApprovalGate):
    gate = gate_service.attach(
        "exec-1", "deploy", timeout_seconds=60, timestamp=BASE_TIME
    )

    with pytest.raises(InvalidGateStateError):
        gate_service.approve(
            gate.gate_id, "alice", timestamp=BASE_TIME + timedelta(minutes=5)
        )


def test_reject_marks_rejected_and_fails_workflow(gate_service: DeploymentApprovalGate):
    wf_engine, execution_id = _running_execution()
    gate = gate_service.attach(execution_id, "deploy", workflow_engine=wf_engine, timestamp=BASE_TIME)

    updated = gate_service.reject(
        gate.gate_id, "alice", reason="not ready", workflow_engine=wf_engine, timestamp=BASE_TIME
    )

    assert updated.status == "REJECTED"
    assert wf_engine.status(execution_id).status == "FAILED"


def test_reject_unauthorized_raises(gate_service: DeploymentApprovalGate):
    gate = gate_service.attach(
        "exec-1", "deploy", approvers=("alice",), timestamp=BASE_TIME
    )

    with pytest.raises(UnauthorizedApproverError):
        gate_service.reject(gate.gate_id, "mallory", timestamp=BASE_TIME)


def test_get_unknown_raises(gate_service: DeploymentApprovalGate):
    with pytest.raises(UnknownGateError):
        gate_service.get("does-not-exist")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_pipeline_router)
    app.include_router(deployment_workflow_router)
    app.include_router(deployment_approval_gate_router)
    return TestClient(app)


def _start_execution(client: TestClient, pipeline_name: str) -> str:
    client.post(
        "/governance/pipelines",
        json={"name": pipeline_name, "stages": [{"name": "deploy", "action": "deploy"}]},
    )
    response = client.post("/governance/workflows/start", json={"pipeline": pipeline_name})
    return response.json()["execution_id"]


def test_api_create_approve_and_get(client: TestClient):
    execution_id = _start_execution(client, "svc-gate-api-1")

    create_response = client.post(
        "/governance/approval-gates",
        json={"execution_id": execution_id, "stage": "deploy"},
    )
    gate_id = create_response.json()["gate_id"]

    approve_response = client.post(
        f"/governance/approval-gates/{gate_id}/approve", json={"approver": "alice"}
    )
    get_response = client.get(f"/governance/approval-gates/{gate_id}")

    assert create_response.status_code == 200
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "APPROVED"
    assert get_response.status_code == 200


def test_api_create_requires_execution_id_and_stage(client: TestClient):
    response = client.post("/governance/approval-gates", json={})

    assert response.status_code == 422


def test_api_reject_fails_workflow(client: TestClient):
    execution_id = _start_execution(client, "svc-gate-api-2")
    create_response = client.post(
        "/governance/approval-gates",
        json={"execution_id": execution_id, "stage": "deploy"},
    )
    gate_id = create_response.json()["gate_id"]

    response = client.post(
        f"/governance/approval-gates/{gate_id}/reject", json={"approver": "alice"}
    )
    workflow_response = client.get(f"/governance/workflows/{execution_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert workflow_response.json()["status"] == "FAILED"


def test_api_approve_unauthorized_returns_403(client: TestClient):
    execution_id = _start_execution(client, "svc-gate-api-3")
    create_response = client.post(
        "/governance/approval-gates",
        json={"execution_id": execution_id, "stage": "deploy", "approvers": ["alice"]},
    )
    gate_id = create_response.json()["gate_id"]

    response = client.post(
        f"/governance/approval-gates/{gate_id}/approve", json={"approver": "mallory"}
    )

    assert response.status_code == 403


def test_api_approve_missing_approver_returns_422(client: TestClient):
    execution_id = _start_execution(client, "svc-gate-api-4")
    create_response = client.post(
        "/governance/approval-gates",
        json={"execution_id": execution_id, "stage": "deploy"},
    )
    gate_id = create_response.json()["gate_id"]

    response = client.post(f"/governance/approval-gates/{gate_id}/approve", json={})

    assert response.status_code == 422


def test_api_get_unknown_gate_returns_404(client: TestClient):
    response = client.get("/governance/approval-gates/does-not-exist")

    assert response.status_code == 404
