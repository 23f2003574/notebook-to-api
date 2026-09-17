from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException

from .etl_engine import ETLWorkflowEngine, get_etl_workflow_engine
from .pipeline_executor import (
    ExecutionState,
    PipelineExecutionEngine,
    PipelineRun,
    UnknownRunError,
    get_pipeline_execution_engine,
)


class CheckpointType(str, Enum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"
    SCHEDULED = "scheduled"
    FINAL = "final"


class UnknownCheckpointError(KeyError):
    pass


@dataclass(frozen=True)
class Checkpoint:
    """A saved snapshot of a run's dataset at a given pipeline stage."""

    checkpoint_id: str
    run_id: str
    workflow_name: str
    checkpoint_type: CheckpointType
    stage: str
    rows: tuple
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "checkpoint_id": self.checkpoint_id,
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "checkpoint_type": self.checkpoint_type.value,
            "stage": self.stage,
            "rows": [dict(row) for row in self.rows],
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class RecoveryState:
    """A record of a single restore or resume attempt against a checkpoint."""

    recovery_id: str
    checkpoint_id: str
    run_id: str
    new_run_id: Optional[str]
    status: str
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "recovery_id": self.recovery_id,
            "checkpoint_id": self.checkpoint_id,
            "run_id": self.run_id,
            "new_run_id": self.new_run_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class CheckpointRecoveryManager:
    """Snapshots run state for later restore, and drives execution resume/recovery."""

    def __init__(self) -> None:
        self._checkpoints: dict = {}
        self._recoveries: list = []
        self._lock = Lock()

    def create_checkpoint(
        self,
        run_id: str,
        workflow_name: str,
        rows: list,
        stage: str = "",
        checkpoint_type: CheckpointType = CheckpointType.AUTOMATIC,
        *,
        executor: Optional[PipelineExecutionEngine] = None,
    ) -> Checkpoint:
        if not run_id:
            raise ValueError("run_id is required")
        if not workflow_name:
            raise ValueError("workflow_name is required")
        if executor is not None:
            executor.status(run_id)
        checkpoint = Checkpoint(
            checkpoint_id=uuid4().hex,
            run_id=run_id,
            workflow_name=workflow_name,
            checkpoint_type=checkpoint_type,
            stage=stage,
            rows=tuple(rows),
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        return checkpoint

    def get(self, checkpoint_id: str) -> Checkpoint:
        with self._lock:
            checkpoint = self._checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise UnknownCheckpointError(checkpoint_id)
        return checkpoint

    def list_checkpoints(self, *, run_id: Optional[str] = None) -> list:
        with self._lock:
            items = list(self._checkpoints.values())
        if run_id is not None:
            items = [checkpoint for checkpoint in items if checkpoint.run_id == run_id]
        return sorted(items, key=lambda checkpoint: checkpoint.created_at)

    def delete_checkpoint(self, checkpoint_id: str) -> None:
        with self._lock:
            if checkpoint_id not in self._checkpoints:
                raise UnknownCheckpointError(checkpoint_id)
            del self._checkpoints[checkpoint_id]

    def restore(self, checkpoint_id: str) -> RecoveryState:
        checkpoint = self.get(checkpoint_id)
        recovery = RecoveryState(
            recovery_id=uuid4().hex,
            checkpoint_id=checkpoint.checkpoint_id,
            run_id=checkpoint.run_id,
            new_run_id=None,
            status="restored",
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._recoveries.append(recovery)
        return recovery

    def resume(
        self,
        checkpoint_id: str,
        executor: PipelineExecutionEngine,
        workflows: ETLWorkflowEngine,
        **execute_kwargs,
    ) -> PipelineRun:
        checkpoint = self.get(checkpoint_id)
        new_run = executor.submit(
            checkpoint.workflow_name, list(checkpoint.rows), resumed_from_checkpoint=checkpoint_id
        )
        finished = executor.execute(new_run.run_id, workflows=workflows, **execute_kwargs)
        recovery = RecoveryState(
            recovery_id=uuid4().hex,
            checkpoint_id=checkpoint.checkpoint_id,
            run_id=checkpoint.run_id,
            new_run_id=finished.run_id,
            status="recovered" if finished.state == ExecutionState.SUCCEEDED else "failed",
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._recoveries.append(recovery)
        return finished

    def list_recoveries(self, *, checkpoint_id: Optional[str] = None) -> list:
        with self._lock:
            items = list(reversed(self._recoveries))
        if checkpoint_id is not None:
            items = [recovery for recovery in items if recovery.checkpoint_id == checkpoint_id]
        return items

    def cleanup(
        self,
        *,
        retention_per_run: Optional[int] = None,
        older_than: Optional[datetime] = None,
    ) -> int:
        removed = 0
        with self._lock:
            if older_than is not None:
                stale_ids = [cid for cid, checkpoint in self._checkpoints.items() if checkpoint.created_at < older_than]
                for checkpoint_id in stale_ids:
                    del self._checkpoints[checkpoint_id]
                    removed += 1
            if retention_per_run is not None:
                by_run: dict = {}
                for checkpoint in self._checkpoints.values():
                    by_run.setdefault(checkpoint.run_id, []).append(checkpoint)
                for checkpoints in by_run.values():
                    checkpoints.sort(key=lambda checkpoint: checkpoint.created_at, reverse=True)
                    for stale_checkpoint in checkpoints[retention_per_run:]:
                        del self._checkpoints[stale_checkpoint.checkpoint_id]
                        removed += 1
        return removed


_checkpoint_recovery_manager = CheckpointRecoveryManager()


def get_checkpoint_recovery_manager() -> CheckpointRecoveryManager:
    return _checkpoint_recovery_manager


router = APIRouter(prefix="/pipelines/checkpoints", tags=["pipeline-checkpoints"])


@router.post("", status_code=201)
def create_checkpoint_endpoint(
    payload: dict = Body(default={}),
    manager: CheckpointRecoveryManager = Depends(get_checkpoint_recovery_manager),
    executor: PipelineExecutionEngine = Depends(get_pipeline_execution_engine),
) -> dict:
    try:
        checkpoint_type = CheckpointType(payload.get("checkpoint_type", CheckpointType.AUTOMATIC.value))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        checkpoint = manager.create_checkpoint(
            payload.get("run_id", ""),
            payload.get("workflow_name", ""),
            payload.get("rows", []),
            payload.get("stage", ""),
            checkpoint_type,
            executor=executor,
        )
    except UnknownRunError:
        raise HTTPException(status_code=404, detail="unknown run")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return checkpoint.to_dict()


@router.get("")
def list_checkpoints_endpoint(
    run_id: Optional[str] = None,
    manager: CheckpointRecoveryManager = Depends(get_checkpoint_recovery_manager),
) -> list:
    return [checkpoint.to_dict() for checkpoint in manager.list_checkpoints(run_id=run_id)]


@router.post("/{checkpoint}/restore")
def restore_checkpoint_endpoint(
    checkpoint: str,
    manager: CheckpointRecoveryManager = Depends(get_checkpoint_recovery_manager),
    executor: PipelineExecutionEngine = Depends(get_pipeline_execution_engine),
    workflows: ETLWorkflowEngine = Depends(get_etl_workflow_engine),
) -> dict:
    try:
        run = manager.resume(checkpoint, executor, workflows)
    except UnknownCheckpointError:
        raise HTTPException(status_code=404, detail="unknown checkpoint")
    return run.to_dict()


@router.delete("/{checkpoint}", status_code=204)
def delete_checkpoint_endpoint(
    checkpoint: str,
    manager: CheckpointRecoveryManager = Depends(get_checkpoint_recovery_manager),
) -> None:
    try:
        manager.delete_checkpoint(checkpoint)
    except UnknownCheckpointError:
        raise HTTPException(status_code=404, detail="unknown checkpoint")
