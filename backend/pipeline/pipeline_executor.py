from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException

from .data_sources import DataSourceManager, get_data_source_manager
from .etl_engine import ETLStage, ETLWorkflowEngine, UnknownWorkflowError, get_etl_workflow_engine
from .pipeline_analytics import PipelineAnalyticsService, get_pipeline_analytics_service
from .pipeline_scheduler import PipelineScheduler
from .transformation_engine import DataTransformationEngine, get_data_transformation_engine


class ExecutionState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UnknownRunError(KeyError):
    pass


class InvalidStateTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class PipelineRun:
    """A single submission of a workflow to the execution engine, with its lifecycle state."""

    run_id: str
    workflow_name: str
    state: ExecutionState
    progress: float
    schedule_id: Optional[str]
    execution_id: Optional[str]
    error: Optional[str]
    submitted_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    resumed_from_checkpoint: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "state": self.state.value,
            "progress": self.progress,
            "schedule_id": self.schedule_id,
            "execution_id": self.execution_id,
            "error": self.error,
            "submitted_at": self.submitted_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "resumed_from_checkpoint": self.resumed_from_checkpoint,
        }


class PipelineExecutionEngine:
    """Runs submitted pipeline workflows through a queued execution lifecycle."""

    def __init__(self) -> None:
        self._runs: dict = {}
        self._pending_rows: dict = {}
        self._lock = Lock()

    def submit(
        self,
        workflow_name: str,
        rows: list,
        *,
        schedule_id: Optional[str] = None,
        resumed_from_checkpoint: Optional[str] = None,
    ) -> PipelineRun:
        if not workflow_name:
            raise ValueError("workflow_name is required")
        run = PipelineRun(
            run_id=uuid4().hex,
            workflow_name=workflow_name,
            state=ExecutionState.QUEUED,
            progress=0.0,
            schedule_id=schedule_id,
            execution_id=None,
            error=None,
            submitted_at=datetime.now(timezone.utc),
            started_at=None,
            finished_at=None,
            resumed_from_checkpoint=resumed_from_checkpoint,
        )
        with self._lock:
            self._runs[run.run_id] = run
            self._pending_rows[run.run_id] = list(rows)
        return run

    def execute(
        self,
        run_id: str,
        *,
        workflows: ETLWorkflowEngine,
        sources: Optional[DataSourceManager] = None,
        transformation_engine: Optional[DataTransformationEngine] = None,
        analytics: Optional[PipelineAnalyticsService] = None,
        **execute_kwargs,
    ) -> PipelineRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise UnknownRunError(run_id)
            if run.state != ExecutionState.QUEUED:
                raise InvalidStateTransitionError(f"cannot execute run in state '{run.state.value}'")
            rows = self._pending_rows.get(run_id, [])
            running = replace(run, state=ExecutionState.RUNNING, started_at=datetime.now(timezone.utc))
            self._runs[run_id] = running

        try:
            result = workflows.execute(
                running.workflow_name,
                rows,
                sources=sources,
                transformation_engine=transformation_engine,
                **execute_kwargs,
            )
        except UnknownWorkflowError as exc:
            with self._lock:
                failed = replace(
                    running,
                    state=ExecutionState.FAILED,
                    progress=0.0,
                    error=f"unknown workflow: {exc}",
                    finished_at=datetime.now(timezone.utc),
                )
                self._runs[run_id] = failed
                self._pending_rows.pop(run_id, None)
            self._record_metrics(analytics, failed, row_count=0)
            return failed

        progress = len(result.stages_completed) / len(ETLStage)
        state = ExecutionState.SUCCEEDED if result.status == "success" else ExecutionState.FAILED
        with self._lock:
            finished = replace(
                running,
                state=state,
                progress=progress,
                execution_id=result.execution_id,
                error=result.error,
                finished_at=datetime.now(timezone.utc),
            )
            self._runs[run_id] = finished
            self._pending_rows.pop(run_id, None)
        self._record_metrics(analytics, finished, row_count=result.row_count)
        return finished

    @staticmethod
    def _record_metrics(analytics: Optional[PipelineAnalyticsService], run: "PipelineRun", *, row_count: int) -> None:
        if analytics is None:
            return
        duration_ms = 0.0
        if run.started_at and run.finished_at:
            duration_ms = (run.finished_at - run.started_at).total_seconds() * 1000
        analytics.record(
            run.workflow_name,
            "success" if run.state == ExecutionState.SUCCEEDED else "failed",
            duration_ms,
            row_count,
        )

    def cancel(self, run_id: str) -> PipelineRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise UnknownRunError(run_id)
            if run.state != ExecutionState.QUEUED:
                raise InvalidStateTransitionError(f"cannot cancel run in state '{run.state.value}'")
            cancelled = replace(run, state=ExecutionState.CANCELLED, finished_at=datetime.now(timezone.utc))
            self._runs[run_id] = cancelled
            self._pending_rows.pop(run_id, None)
            return cancelled

    def status(self, run_id: str) -> PipelineRun:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise UnknownRunError(run_id)
        return run

    def list_runs(self, *, workflow_name: Optional[str] = None, state: Optional[ExecutionState] = None) -> list:
        with self._lock:
            items = list(self._runs.values())
        if workflow_name is not None:
            items = [run for run in items if run.workflow_name == workflow_name]
        if state is not None:
            items = [run for run in items if run.state == state]
        return sorted(items, key=lambda run: run.submitted_at, reverse=True)

    def dispatch_due(
        self,
        scheduler: PipelineScheduler,
        workflows: ETLWorkflowEngine,
        *,
        now: Optional[datetime] = None,
        **execute_kwargs,
    ) -> list:
        now = now or datetime.now(timezone.utc)
        results = []
        for schedule in scheduler.due(now):
            run = self.submit(schedule.workflow_name, [], schedule_id=schedule.schedule_id)
            finished = self.execute(run.run_id, workflows=workflows, **execute_kwargs)
            scheduler.mark_dispatched(schedule.schedule_id, now=now)
            results.append(finished)
        return results


_pipeline_execution_engine = PipelineExecutionEngine()


def get_pipeline_execution_engine() -> PipelineExecutionEngine:
    return _pipeline_execution_engine


router = APIRouter(prefix="/pipelines", tags=["pipeline-execution"])


@router.post("/execute")
def execute_endpoint(
    payload: dict = Body(default={}),
    engine: PipelineExecutionEngine = Depends(get_pipeline_execution_engine),
    workflows: ETLWorkflowEngine = Depends(get_etl_workflow_engine),
    sources: DataSourceManager = Depends(get_data_source_manager),
    transformation_engine: DataTransformationEngine = Depends(get_data_transformation_engine),
    analytics: PipelineAnalyticsService = Depends(get_pipeline_analytics_service),
) -> dict:
    try:
        run = engine.submit(payload.get("workflow_name", ""), payload.get("rows", []))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finished = engine.execute(
        run.run_id,
        workflows=workflows,
        sources=sources,
        transformation_engine=transformation_engine,
        analytics=analytics,
    )
    return finished.to_dict()


@router.get("/runs")
def list_runs_endpoint(
    workflow_name: Optional[str] = None,
    engine: PipelineExecutionEngine = Depends(get_pipeline_execution_engine),
) -> list:
    return [run.to_dict() for run in engine.list_runs(workflow_name=workflow_name)]


@router.get("/runs/{run}")
def get_run_endpoint(
    run: str,
    engine: PipelineExecutionEngine = Depends(get_pipeline_execution_engine),
) -> dict:
    try:
        return engine.status(run).to_dict()
    except UnknownRunError:
        raise HTTPException(status_code=404, detail="unknown run")


@router.delete("/runs/{run}", status_code=204)
def cancel_run_endpoint(
    run: str,
    engine: PipelineExecutionEngine = Depends(get_pipeline_execution_engine),
) -> None:
    try:
        engine.cancel(run)
    except UnknownRunError:
        raise HTTPException(status_code=404, detail="unknown run")
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
