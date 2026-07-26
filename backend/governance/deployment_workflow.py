from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Optional

from fastapi import APIRouter, Body, HTTPException

from .deployment_pipeline import DeploymentPipelineEngine, UnknownPipelineError

WORKFLOW_STATUSES = ("RUNNING", "PAUSED", "CANCELLED", "COMPLETED", "FAILED")

_TRANSITIONS = {
    "pause": (("RUNNING",), "PAUSED"),
    "resume": (("PAUSED",), "RUNNING"),
    "cancel": (("RUNNING", "PAUSED"), "CANCELLED"),
    "complete": (("RUNNING",), "COMPLETED"),
    "fail": (("RUNNING", "PAUSED"), "FAILED"),
    "recover": (("FAILED", "PAUSED"), "RUNNING"),
}


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownExecutionError(KeyError):
    pass


class InvalidTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionState:
    """One immutable state transition in a workflow execution's history."""

    status: str
    transitioned_at: datetime

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "transitioned_at": self.transitioned_at.isoformat(),
        }


@dataclass(frozen=True)
class WorkflowExecution:
    """An immutable snapshot of a running deployment pipeline execution."""

    execution_id: str
    pipeline: str
    status: str
    stages: tuple = ()
    context: dict = field(default_factory=dict)
    history: tuple = ()
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "pipeline": self.pipeline,
            "status": self.status,
            "stages": list(self.stages),
            "context": dict(self.context),
            "history": [state.to_dict() for state in self.history],
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeploymentWorkflowEngine:
    """Manages the lifecycle of deployment pipeline executions."""

    def __init__(self, pipeline_engine: Optional[DeploymentPipelineEngine] = None) -> None:
        self._executions: dict[str, WorkflowExecution] = {}
        self._hooks: list[Callable[[WorkflowExecution], None]] = []
        self._lock = Lock()
        self._pipeline_engine = pipeline_engine

    def on_transition(self, hook: Callable[[WorkflowExecution], None]) -> None:
        with self._lock:
            self._hooks.append(hook)

    def start(
        self,
        pipeline: str,
        *,
        context: Optional[dict] = None,
        pipeline_engine: Optional[DeploymentPipelineEngine] = None,
        timestamp: Optional[datetime] = None,
    ) -> WorkflowExecution:
        if not pipeline:
            raise ValueError("pipeline is required")

        engine = pipeline_engine or self._pipeline_engine
        stages: tuple = ()
        if engine is not None:
            stages = engine.stage_names(pipeline)

        now = timestamp or datetime.now(timezone.utc)
        execution = WorkflowExecution(
            execution_id=_new_id(),
            pipeline=pipeline,
            status="RUNNING",
            stages=stages,
            context=dict(context or {}),
            history=(ExecutionState(status="RUNNING", transitioned_at=now),),
            started_at=now,
            updated_at=now,
        )
        with self._lock:
            self._executions[execution.execution_id] = execution
        self._notify(execution)
        return execution

    def pause(
        self, execution_id: str, *, timestamp: Optional[datetime] = None
    ) -> WorkflowExecution:
        return self._transition(execution_id, "pause", timestamp=timestamp)

    def resume(
        self, execution_id: str, *, timestamp: Optional[datetime] = None
    ) -> WorkflowExecution:
        return self._transition(execution_id, "resume", timestamp=timestamp)

    def cancel(
        self, execution_id: str, *, timestamp: Optional[datetime] = None
    ) -> WorkflowExecution:
        return self._transition(execution_id, "cancel", timestamp=timestamp)

    def complete(
        self, execution_id: str, *, timestamp: Optional[datetime] = None
    ) -> WorkflowExecution:
        return self._transition(execution_id, "complete", timestamp=timestamp)

    def fail(
        self, execution_id: str, *, timestamp: Optional[datetime] = None
    ) -> WorkflowExecution:
        return self._transition(execution_id, "fail", timestamp=timestamp)

    def recover(
        self, execution_id: str, *, timestamp: Optional[datetime] = None
    ) -> WorkflowExecution:
        return self._transition(execution_id, "recover", timestamp=timestamp)

    def status(self, execution_id: str) -> WorkflowExecution:
        with self._lock:
            execution = self._executions.get(execution_id)
        if execution is None:
            raise UnknownExecutionError(execution_id)
        return execution

    def executions_for(self, pipeline: str) -> tuple:
        with self._lock:
            return tuple(
                execution
                for execution in self._executions.values()
                if execution.pipeline == pipeline
            )

    def pause_if_running(
        self, execution_id: str, *, timestamp: Optional[datetime] = None
    ) -> WorkflowExecution:
        execution = self.status(execution_id)
        if execution.status == "RUNNING":
            return self.pause(execution_id, timestamp=timestamp)
        return execution

    def _transition(
        self, execution_id: str, action: str, *, timestamp: Optional[datetime] = None
    ) -> WorkflowExecution:
        allowed_from, new_status = _TRANSITIONS[action]
        with self._lock:
            execution = self._executions.get(execution_id)
        if execution is None:
            raise UnknownExecutionError(execution_id)
        if execution.status not in allowed_from:
            raise InvalidTransitionError(
                f"cannot {action} an execution in status {execution.status}"
            )

        now = timestamp or datetime.now(timezone.utc)
        updated = WorkflowExecution(
            execution_id=execution.execution_id,
            pipeline=execution.pipeline,
            status=new_status,
            stages=execution.stages,
            context=execution.context,
            history=execution.history + (ExecutionState(status=new_status, transitioned_at=now),),
            started_at=execution.started_at,
            updated_at=now,
        )
        with self._lock:
            self._executions[execution_id] = updated
        self._notify(updated)
        return updated

    def _notify(self, execution: WorkflowExecution) -> None:
        for hook in list(self._hooks):
            hook(execution)


_engine = DeploymentWorkflowEngine()


def get_deployment_workflow_engine() -> DeploymentWorkflowEngine:
    return _engine


router = APIRouter(prefix="/governance", tags=["governance-workflows"])


@router.post("/workflows/start")
def start_workflow(payload: dict = Body(...)) -> dict:
    from .deployment_pipeline import get_deployment_pipeline_engine

    pipeline = payload.get("pipeline")
    if not pipeline:
        raise HTTPException(status_code=422, detail="pipeline is required")

    try:
        execution = get_deployment_workflow_engine().start(
            pipeline,
            context=payload.get("context"),
            pipeline_engine=get_deployment_pipeline_engine(),
        )
    except UnknownPipelineError:
        raise HTTPException(status_code=404, detail="unknown pipeline")
    return execution.to_dict()


@router.post("/workflows/{execution_id}/pause")
def pause_workflow(execution_id: str) -> dict:
    try:
        execution = get_deployment_workflow_engine().pause(execution_id)
    except UnknownExecutionError:
        raise HTTPException(status_code=404, detail="unknown execution")
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return execution.to_dict()


@router.post("/workflows/{execution_id}/resume")
def resume_workflow(execution_id: str) -> dict:
    try:
        execution = get_deployment_workflow_engine().resume(execution_id)
    except UnknownExecutionError:
        raise HTTPException(status_code=404, detail="unknown execution")
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return execution.to_dict()


@router.get("/workflows/{execution_id}")
def get_workflow(execution_id: str) -> dict:
    try:
        execution = get_deployment_workflow_engine().status(execution_id)
    except UnknownExecutionError:
        raise HTTPException(status_code=404, detail="unknown execution")
    return execution.to_dict()
