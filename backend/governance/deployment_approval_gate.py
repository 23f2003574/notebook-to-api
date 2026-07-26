from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .deployment_workflow import DeploymentWorkflowEngine

GATE_STATUSES = ("PENDING", "APPROVED", "REJECTED", "EXPIRED")


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownGateError(KeyError):
    pass


class InvalidGateStateError(RuntimeError):
    pass


class UnauthorizedApproverError(PermissionError):
    pass


class DuplicateDecisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class GateDecision:
    """One immutable approve()/reject() decision recorded against a gate."""

    approver: str
    approved: bool
    reason: Optional[str] = None
    decided_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "approver": self.approver,
            "approved": self.approved,
            "reason": self.reason,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
        }


@dataclass(frozen=True)
class ApprovalGate:
    """An immutable snapshot of a stage-level approval checkpoint."""

    gate_id: str
    execution_id: str
    stage: str
    required_approvals: int
    approvers: tuple = ()
    delegates: dict = field(default_factory=dict)
    status: str = "PENDING"
    decisions: tuple = ()
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "gate_id": self.gate_id,
            "execution_id": self.execution_id,
            "stage": self.stage,
            "required_approvals": self.required_approvals,
            "approvers": list(self.approvers),
            "delegates": dict(self.delegates),
            "status": self.status,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeploymentApprovalGate:
    """Gates a pipeline stage's execution behind one or more approval decisions."""

    def __init__(self, workflow_engine: Optional[DeploymentWorkflowEngine] = None) -> None:
        self._gates: dict[str, ApprovalGate] = {}
        self._lock = Lock()
        self._workflow_engine = workflow_engine

    def attach(
        self,
        execution_id: str,
        stage: str,
        *,
        required_approvals: int = 1,
        approvers=(),
        delegates: Optional[dict] = None,
        timeout_seconds: Optional[float] = None,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        timestamp: Optional[datetime] = None,
    ) -> ApprovalGate:
        if not execution_id:
            raise ValueError("execution_id is required")
        if not stage:
            raise ValueError("stage is required")
        if required_approvals < 1:
            raise ValueError("required_approvals must be at least 1")

        now = timestamp or datetime.now(timezone.utc)
        gate = ApprovalGate(
            gate_id=_new_id(),
            execution_id=execution_id,
            stage=stage,
            required_approvals=required_approvals,
            approvers=tuple(approvers),
            delegates=dict(delegates or {}),
            status="PENDING",
            expires_at=now + timedelta(seconds=timeout_seconds) if timeout_seconds else None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._gates[gate.gate_id] = gate

        engine = workflow_engine or self._workflow_engine
        if engine is not None:
            engine.pause_if_running(execution_id, timestamp=now)
        return gate

    def evaluate(
        self,
        gate_id: str,
        *,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        timestamp: Optional[datetime] = None,
    ) -> ApprovalGate:
        gate = self.get(gate_id)
        if gate.status != "PENDING":
            return gate

        now = timestamp or datetime.now(timezone.utc)
        if gate.expires_at is None or now < gate.expires_at:
            return gate

        updated = replace(gate, status="EXPIRED", updated_at=now)
        with self._lock:
            self._gates[gate_id] = updated

        engine = workflow_engine or self._workflow_engine
        if engine is not None:
            engine.fail(gate.execution_id, timestamp=now)
        return updated

    def approve(
        self,
        gate_id: str,
        approver: str,
        *,
        reason: Optional[str] = None,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        timestamp: Optional[datetime] = None,
    ) -> ApprovalGate:
        return self._decide(
            gate_id,
            approver,
            approved=True,
            reason=reason,
            workflow_engine=workflow_engine,
            timestamp=timestamp,
        )

    def reject(
        self,
        gate_id: str,
        approver: str,
        *,
        reason: Optional[str] = None,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        timestamp: Optional[datetime] = None,
    ) -> ApprovalGate:
        return self._decide(
            gate_id,
            approver,
            approved=False,
            reason=reason,
            workflow_engine=workflow_engine,
            timestamp=timestamp,
        )

    def get(self, gate_id: str) -> ApprovalGate:
        with self._lock:
            gate = self._gates.get(gate_id)
        if gate is None:
            raise UnknownGateError(gate_id)
        return gate

    def _resolve_principal(self, gate: ApprovalGate, actor: str) -> str:
        if not gate.approvers or actor in gate.approvers:
            return actor
        for original, delegate in gate.delegates.items():
            if delegate == actor and original in gate.approvers:
                return original
        raise UnauthorizedApproverError(actor)

    def _decide(
        self,
        gate_id: str,
        actor: str,
        *,
        approved: bool,
        reason: Optional[str],
        workflow_engine: Optional[DeploymentWorkflowEngine],
        timestamp: Optional[datetime],
    ) -> ApprovalGate:
        now = timestamp or datetime.now(timezone.utc)
        gate = self.evaluate(gate_id, workflow_engine=workflow_engine, timestamp=now)
        if gate.status != "PENDING":
            raise InvalidGateStateError(
                f"gate '{gate_id}' is not pending (status={gate.status})"
            )

        principal = self._resolve_principal(gate, actor)
        if any(decision.approver == principal for decision in gate.decisions):
            raise DuplicateDecisionError(
                f"'{principal}' has already decided gate '{gate_id}'"
            )

        decision = GateDecision(approver=principal, approved=approved, reason=reason, decided_at=now)
        decisions = gate.decisions + (decision,)
        engine = workflow_engine or self._workflow_engine

        if not approved:
            updated = replace(gate, decisions=decisions, status="REJECTED", updated_at=now)
            with self._lock:
                self._gates[gate_id] = updated
            if engine is not None:
                engine.fail(gate.execution_id, timestamp=now)
            return updated

        approvals = sum(1 for decision in decisions if decision.approved)
        new_status = "APPROVED" if approvals >= gate.required_approvals else "PENDING"
        updated = replace(gate, decisions=decisions, status=new_status, updated_at=now)
        with self._lock:
            self._gates[gate_id] = updated
        if new_status == "APPROVED" and engine is not None:
            engine.resume(gate.execution_id, timestamp=now)
        return updated


_gate_service = DeploymentApprovalGate()


def get_deployment_approval_gate() -> DeploymentApprovalGate:
    return _gate_service


router = APIRouter(prefix="/governance", tags=["governance-approval-gates"])


@router.post("/approval-gates")
def create_gate(payload: dict = Body(...)) -> dict:
    from .deployment_workflow import get_deployment_workflow_engine

    execution_id = payload.get("execution_id")
    stage = payload.get("stage")
    if not execution_id or not stage:
        raise HTTPException(status_code=422, detail="execution_id and stage are required")

    try:
        gate = get_deployment_approval_gate().attach(
            execution_id,
            stage,
            required_approvals=payload.get("required_approvals", 1),
            approvers=payload.get("approvers", ()),
            delegates=payload.get("delegates"),
            timeout_seconds=payload.get("timeout_seconds"),
            workflow_engine=get_deployment_workflow_engine(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return gate.to_dict()


@router.post("/approval-gates/{gate_id}/approve")
def approve_gate(gate_id: str, payload: dict = Body(...)) -> dict:
    from .deployment_workflow import get_deployment_workflow_engine

    approver = payload.get("approver")
    if not approver:
        raise HTTPException(status_code=422, detail="approver is required")

    try:
        gate = get_deployment_approval_gate().approve(
            gate_id,
            approver,
            reason=payload.get("reason"),
            workflow_engine=get_deployment_workflow_engine(),
        )
    except UnknownGateError:
        raise HTTPException(status_code=404, detail="unknown approval gate")
    except UnauthorizedApproverError:
        raise HTTPException(status_code=403, detail="not authorized to decide this gate")
    except (InvalidGateStateError, DuplicateDecisionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return gate.to_dict()


@router.post("/approval-gates/{gate_id}/reject")
def reject_gate(gate_id: str, payload: dict = Body(...)) -> dict:
    from .deployment_workflow import get_deployment_workflow_engine

    approver = payload.get("approver")
    if not approver:
        raise HTTPException(status_code=422, detail="approver is required")

    try:
        gate = get_deployment_approval_gate().reject(
            gate_id,
            approver,
            reason=payload.get("reason"),
            workflow_engine=get_deployment_workflow_engine(),
        )
    except UnknownGateError:
        raise HTTPException(status_code=404, detail="unknown approval gate")
    except UnauthorizedApproverError:
        raise HTTPException(status_code=403, detail="not authorized to decide this gate")
    except (InvalidGateStateError, DuplicateDecisionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return gate.to_dict()


@router.get("/approval-gates/{gate_id}")
def get_gate(gate_id: str) -> dict:
    from .deployment_workflow import get_deployment_workflow_engine

    try:
        gate = get_deployment_approval_gate().evaluate(
            gate_id, workflow_engine=get_deployment_workflow_engine()
        )
    except UnknownGateError:
        raise HTTPException(status_code=404, detail="unknown approval gate")
    return gate.to_dict()
