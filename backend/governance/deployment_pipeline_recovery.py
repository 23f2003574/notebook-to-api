from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .deployment_recovery import (
    DeploymentRecoveryCoordinator,
    NoApplicableStrategyError,
    UnknownStrategyError,
)
from .deployment_stage_orchestrator import DeploymentStageOrchestrator
from .deployment_workflow import DeploymentWorkflowEngine, InvalidTransitionError

RECOVERY_MODES = ("resume", "restart_stage", "restart_pipeline", "rollback")


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class RecoveryAction:
    """One immutable outcome of applying a single recovery mode."""

    mode: str
    status: str
    message: str
    target: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "status": self.status,
            "message": self.message,
            "target": self.target,
        }


@dataclass(frozen=True)
class PipelineRecovery:
    """An immutable record of one recovery attempt against a pipeline execution."""

    recovery_id: str
    execution_id: str
    action: RecoveryAction
    recorded_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "recovery_id": self.recovery_id,
            "execution_id": self.execution_id,
            "action": self.action.to_dict(),
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
        }


class DeploymentPipelineRecoveryManager:
    """Coordinates pipeline-level recovery, delegating rollback to the existing recovery subsystem."""

    def __init__(
        self,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        stage_orchestrator: Optional[DeploymentStageOrchestrator] = None,
        recovery_coordinator: Optional[DeploymentRecoveryCoordinator] = None,
    ) -> None:
        self._history: dict[str, list] = {}
        self._lock = Lock()
        self._workflow_engine = workflow_engine
        self._stage_orchestrator = stage_orchestrator
        self._recovery_coordinator = recovery_coordinator

    def resume(
        self,
        execution_id: str,
        *,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        timestamp: Optional[datetime] = None,
    ) -> PipelineRecovery:
        engine = workflow_engine or self._workflow_engine
        if engine is None:
            raise ValueError("workflow_engine is required")

        now = timestamp or datetime.now(timezone.utc)
        engine.status(execution_id)

        try:
            execution = engine.recover(execution_id, timestamp=now)
            action = RecoveryAction(
                mode="resume",
                status="SUCCEEDED",
                message=f"execution resumed to status {execution.status}",
            )
        except InvalidTransitionError as exc:
            action = RecoveryAction(mode="resume", status="FAILED", message=str(exc))

        return self._record(execution_id, action, now)

    def restart(
        self,
        execution_id: str,
        *,
        scope: str = "pipeline",
        stage: Optional[str] = None,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        stage_orchestrator: Optional[DeploymentStageOrchestrator] = None,
        timestamp: Optional[datetime] = None,
    ) -> PipelineRecovery:
        if scope not in ("stage", "pipeline"):
            raise ValueError(f"unsupported restart scope '{scope}'")

        engine = workflow_engine or self._workflow_engine
        if engine is None:
            raise ValueError("workflow_engine is required")

        now = timestamp or datetime.now(timezone.utc)
        execution = engine.status(execution_id)

        if scope == "stage":
            if not stage:
                raise ValueError("stage is required when scope='stage'")
            orchestrator = stage_orchestrator or self._stage_orchestrator
            if orchestrator is None:
                raise ValueError("stage_orchestrator is required for stage restarts")
            try:
                result = orchestrator.retry_stage(
                    execution_id, stage, workflow_engine=engine, timestamp=now
                )
                action = RecoveryAction(
                    mode="restart_stage",
                    status="SUCCEEDED" if result.status == "SUCCEEDED" else "FAILED",
                    message=f"stage '{stage}' retried with outcome {result.status}",
                    target=stage,
                )
            except (KeyError, RuntimeError) as exc:
                action = RecoveryAction(
                    mode="restart_stage", status="FAILED", message=str(exc), target=stage
                )
        else:
            try:
                new_execution = engine.start(execution.pipeline, timestamp=now)
                action = RecoveryAction(
                    mode="restart_pipeline",
                    status="SUCCEEDED",
                    message=(
                        f"restarted pipeline '{execution.pipeline}' as execution "
                        f"'{new_execution.execution_id}'"
                    ),
                    target=new_execution.execution_id,
                )
            except (KeyError, RuntimeError, ValueError) as exc:
                action = RecoveryAction(
                    mode="restart_pipeline", status="FAILED", message=str(exc)
                )

        return self._record(execution_id, action, now)

    def recover(
        self,
        execution_id: str,
        mode: str,
        *,
        stage: Optional[str] = None,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        stage_orchestrator: Optional[DeploymentStageOrchestrator] = None,
        recovery_coordinator: Optional[DeploymentRecoveryCoordinator] = None,
        timestamp: Optional[datetime] = None,
    ) -> PipelineRecovery:
        if mode not in RECOVERY_MODES:
            raise ValueError(f"unsupported recovery mode '{mode}'")

        now = timestamp or datetime.now(timezone.utc)

        if mode == "resume":
            return self.resume(execution_id, workflow_engine=workflow_engine, timestamp=now)
        if mode == "restart_stage":
            return self.restart(
                execution_id,
                scope="stage",
                stage=stage,
                workflow_engine=workflow_engine,
                stage_orchestrator=stage_orchestrator,
                timestamp=now,
            )
        if mode == "restart_pipeline":
            return self.restart(
                execution_id, scope="pipeline", workflow_engine=workflow_engine, timestamp=now
            )

        engine = workflow_engine or self._workflow_engine
        if engine is None:
            raise ValueError("workflow_engine is required")
        execution = engine.status(execution_id)

        coordinator = recovery_coordinator or self._recovery_coordinator
        if coordinator is None:
            raise ValueError("recovery_coordinator is required for rollback")

        try:
            record = coordinator.recover(
                {
                    "deployment": execution.pipeline,
                    "execution_id": execution_id,
                    "has_previous_version": True,
                },
                strategy_name="rollback",
                timestamp=now,
            )
            action = RecoveryAction(
                mode="rollback",
                status=record.status,
                message=record.message,
                target=record.recovery_id,
            )
        except (UnknownStrategyError, NoApplicableStrategyError) as exc:
            action = RecoveryAction(mode="rollback", status="FAILED", message=str(exc))

        return self._record(execution_id, action, now)

    def history(self, execution_id: str) -> tuple:
        with self._lock:
            return tuple(self._history.get(execution_id, ()))

    def _record(
        self, execution_id: str, action: RecoveryAction, timestamp: datetime
    ) -> PipelineRecovery:
        record = PipelineRecovery(
            recovery_id=_new_id(),
            execution_id=execution_id,
            action=action,
            recorded_at=timestamp,
        )
        with self._lock:
            self._history.setdefault(execution_id, []).append(record)
        return record


_manager = DeploymentPipelineRecoveryManager()


def get_deployment_pipeline_recovery_manager() -> DeploymentPipelineRecoveryManager:
    return _manager


router = APIRouter(prefix="/governance", tags=["governance-pipeline-recovery"])


@router.post("/pipeline-recovery/{execution_id}/recover")
def recover_execution(execution_id: str, payload: dict = Body(...)) -> dict:
    from .deployment_recovery import get_deployment_recovery_coordinator
    from .deployment_stage_orchestrator import get_deployment_stage_orchestrator
    from .deployment_workflow import UnknownExecutionError, get_deployment_workflow_engine

    mode = payload.get("mode")
    if not mode:
        raise HTTPException(status_code=422, detail="mode is required")

    try:
        record = get_deployment_pipeline_recovery_manager().recover(
            execution_id,
            mode,
            stage=payload.get("stage"),
            workflow_engine=get_deployment_workflow_engine(),
            stage_orchestrator=get_deployment_stage_orchestrator(),
            recovery_coordinator=get_deployment_recovery_coordinator(),
        )
    except UnknownExecutionError:
        raise HTTPException(status_code=404, detail="unknown execution")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return record.to_dict()


@router.post("/pipeline-recovery/{execution_id}/resume")
def resume_execution(execution_id: str) -> dict:
    from .deployment_workflow import UnknownExecutionError, get_deployment_workflow_engine

    try:
        record = get_deployment_pipeline_recovery_manager().resume(
            execution_id, workflow_engine=get_deployment_workflow_engine()
        )
    except UnknownExecutionError:
        raise HTTPException(status_code=404, detail="unknown execution")
    return record.to_dict()


@router.get("/pipeline-recovery/{execution_id}/history")
def get_recovery_history(execution_id: str) -> list:
    records = get_deployment_pipeline_recovery_manager().history(execution_id)
    return [record.to_dict() for record in records]
