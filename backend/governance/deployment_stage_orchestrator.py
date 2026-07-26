from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Optional

from fastapi import APIRouter, Body, HTTPException

from .deployment_workflow import DeploymentWorkflowEngine, UnknownExecutionError

STAGE_RESULT_STATUSES = ("SUCCEEDED", "FAILED", "TIMED_OUT", "SKIPPED")


class UnknownStageError(KeyError):
    pass


class OutOfSequenceError(RuntimeError):
    pass


class StageAlreadyExecutedError(RuntimeError):
    pass


class InvalidRetryError(RuntimeError):
    pass


class RetryLimitExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class StageResult:
    """One immutable outcome of a single stage execution attempt."""

    execution_id: str
    stage: str
    status: str
    message: str
    attempt: int
    started_at: datetime
    ended_at: datetime
    duration_seconds: float

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "stage": self.stage,
            "status": self.status,
            "message": self.message,
            "attempt": self.attempt,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class StageExecution:
    """An immutable, cumulative view of every attempt made at a stage."""

    execution_id: str
    stage: str
    status: str
    attempts: int
    results: tuple = ()
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "stage": self.stage,
            "status": self.status,
            "attempts": self.attempts,
            "results": [result.to_dict() for result in self.results],
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeploymentStageOrchestrator:
    """Coordinates sequential execution of a pipeline's stages."""

    def __init__(
        self,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        *,
        max_retries: int = 3,
    ) -> None:
        self._progress: dict[str, dict[str, StageExecution]] = {}
        self._latest_by_stage: dict[str, StageExecution] = {}
        self._hooks: list[Callable[[StageResult], None]] = []
        self._lock = Lock()
        self._workflow_engine = workflow_engine
        self._max_retries = max_retries

    def on_event(self, hook: Callable[[StageResult], None]) -> None:
        with self._lock:
            self._hooks.append(hook)

    def next_stage(
        self,
        execution_id: str,
        *,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
    ) -> Optional[str]:
        engine = workflow_engine or self._workflow_engine
        stages = self._stage_sequence(execution_id, engine)
        with self._lock:
            completed = {
                stage
                for stage, execution in self._progress.get(execution_id, {}).items()
                if execution.status in ("SUCCEEDED", "SKIPPED")
            }
        for stage in stages:
            if stage not in completed:
                return stage
        return None

    def execute_stage(
        self,
        execution_id: str,
        stage: str,
        *,
        action: Optional[Callable[[], str]] = None,
        timeout_seconds: float = 30.0,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        clock: Callable[[], float] = time.monotonic,
        timestamp: Optional[datetime] = None,
    ) -> StageResult:
        engine = workflow_engine or self._workflow_engine
        stages = self._stage_sequence(execution_id, engine)
        if stage not in stages:
            raise UnknownStageError(stage)

        with self._lock:
            existing = self._progress.get(execution_id, {}).get(stage)
        if existing is not None:
            raise StageAlreadyExecutedError(
                f"stage '{stage}' has already been executed; use retry_stage or skip_stage"
            )

        expected = self.next_stage(execution_id, workflow_engine=engine)
        if expected is not None and expected != stage:
            raise OutOfSequenceError(f"stage '{stage}' cannot run before '{expected}'")

        result = self._run(execution_id, stage, action, timeout_seconds, clock, timestamp, attempt=1)
        self._maybe_complete_workflow(execution_id, engine, timestamp)
        return result

    def retry_stage(
        self,
        execution_id: str,
        stage: str,
        *,
        action: Optional[Callable[[], str]] = None,
        timeout_seconds: float = 30.0,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        clock: Callable[[], float] = time.monotonic,
        timestamp: Optional[datetime] = None,
    ) -> StageResult:
        engine = workflow_engine or self._workflow_engine
        stages = self._stage_sequence(execution_id, engine)
        if stage not in stages:
            raise UnknownStageError(stage)

        with self._lock:
            existing = self._progress.get(execution_id, {}).get(stage)
        if existing is None or existing.status not in ("FAILED", "TIMED_OUT"):
            raise InvalidRetryError(f"stage '{stage}' is not in a retryable state")
        if existing.attempts >= self._max_retries:
            if engine is not None:
                engine.fail(execution_id, timestamp=timestamp)
            raise RetryLimitExceededError(stage)

        result = self._run(
            execution_id, stage, action, timeout_seconds, clock, timestamp, attempt=existing.attempts + 1
        )
        self._maybe_complete_workflow(execution_id, engine, timestamp)
        return result

    def skip_stage(
        self,
        execution_id: str,
        stage: str,
        *,
        reason: Optional[str] = None,
        workflow_engine: Optional[DeploymentWorkflowEngine] = None,
        timestamp: Optional[datetime] = None,
    ) -> StageResult:
        engine = workflow_engine or self._workflow_engine
        stages = self._stage_sequence(execution_id, engine)
        if stage not in stages:
            raise UnknownStageError(stage)

        with self._lock:
            existing = self._progress.get(execution_id, {}).get(stage)
        attempt = existing.attempts if existing else 0
        now = timestamp or datetime.now(timezone.utc)
        result = StageResult(
            execution_id=execution_id,
            stage=stage,
            status="SKIPPED",
            message=reason or "stage skipped",
            attempt=attempt,
            started_at=now,
            ended_at=now,
            duration_seconds=0.0,
        )
        self._record(execution_id, stage, result)
        self._notify(result)
        self._maybe_complete_workflow(execution_id, engine, timestamp)
        return result

    def get_stage(self, stage: str) -> StageExecution:
        with self._lock:
            execution = self._latest_by_stage.get(stage)
        if execution is None:
            raise UnknownStageError(stage)
        return execution

    def _stage_sequence(
        self, execution_id: str, engine: Optional[DeploymentWorkflowEngine]
    ) -> tuple:
        if engine is None:
            raise ValueError("workflow_engine is required to resolve stage sequence")
        return engine.status(execution_id).stages

    def _run(
        self,
        execution_id: str,
        stage: str,
        action: Optional[Callable[[], str]],
        timeout_seconds: float,
        clock: Callable[[], float],
        timestamp: Optional[datetime],
        attempt: int,
    ) -> StageResult:
        started_at = timestamp or datetime.now(timezone.utc)
        start_clock = clock()
        try:
            message = (action or (lambda: "stage completed"))()
        except Exception as exc:  # noqa: BLE001 - stage failures are data
            elapsed = clock() - start_clock
            result = StageResult(
                execution_id=execution_id,
                stage=stage,
                status="FAILED",
                message=str(exc),
                attempt=attempt,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc),
                duration_seconds=elapsed,
            )
        else:
            elapsed = clock() - start_clock
            if elapsed > timeout_seconds:
                result = StageResult(
                    execution_id=execution_id,
                    stage=stage,
                    status="TIMED_OUT",
                    message=f"stage exceeded timeout of {timeout_seconds}s",
                    attempt=attempt,
                    started_at=started_at,
                    ended_at=datetime.now(timezone.utc),
                    duration_seconds=elapsed,
                )
            else:
                result = StageResult(
                    execution_id=execution_id,
                    stage=stage,
                    status="SUCCEEDED",
                    message=message,
                    attempt=attempt,
                    started_at=started_at,
                    ended_at=datetime.now(timezone.utc),
                    duration_seconds=elapsed,
                )
        self._record(execution_id, stage, result)
        self._notify(result)
        return result

    def _record(self, execution_id: str, stage: str, result: StageResult) -> StageExecution:
        with self._lock:
            per_execution = self._progress.setdefault(execution_id, {})
            existing = per_execution.get(stage)
            prior_results = existing.results if existing else ()
            updated = StageExecution(
                execution_id=execution_id,
                stage=stage,
                status=result.status,
                attempts=result.attempt,
                results=prior_results + (result,),
                updated_at=result.ended_at,
            )
            per_execution[stage] = updated
            self._latest_by_stage[stage] = updated
        return updated

    def _maybe_complete_workflow(
        self,
        execution_id: str,
        engine: Optional[DeploymentWorkflowEngine],
        timestamp: Optional[datetime],
    ) -> None:
        if engine is None:
            return
        if self.next_stage(execution_id, workflow_engine=engine) is None:
            engine.complete(execution_id, timestamp=timestamp)

    def _notify(self, result: StageResult) -> None:
        for hook in list(self._hooks):
            hook(result)


_orchestrator = DeploymentStageOrchestrator()


def get_deployment_stage_orchestrator() -> DeploymentStageOrchestrator:
    return _orchestrator


router = APIRouter(prefix="/governance", tags=["governance-stage-orchestrator"])


def _build_action(payload: dict) -> Optional[Callable[[], str]]:
    if payload.get("fail"):
        def _action() -> str:
            raise RuntimeError(payload.get("failure_message", "forced failure"))

        return _action

    duration = payload.get("duration_seconds")
    if duration:
        def _action() -> str:
            time.sleep(duration)
            return "stage completed"

        return _action

    return None


@router.post("/stages/{stage}/execute")
def execute_stage_endpoint(stage: str, payload: dict = Body(default={})) -> dict:
    from .deployment_workflow import get_deployment_workflow_engine

    execution_id = payload.get("execution_id")
    if not execution_id:
        raise HTTPException(status_code=422, detail="execution_id is required")
    timeout_seconds = payload.get("timeout_seconds", 30.0)

    try:
        result = get_deployment_stage_orchestrator().execute_stage(
            execution_id,
            stage,
            action=_build_action(payload),
            timeout_seconds=timeout_seconds,
            workflow_engine=get_deployment_workflow_engine(),
        )
    except UnknownExecutionError:
        raise HTTPException(status_code=404, detail="unknown execution")
    except UnknownStageError:
        raise HTTPException(status_code=404, detail="unknown stage")
    except (OutOfSequenceError, StageAlreadyExecutedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return result.to_dict()


@router.post("/stages/{stage}/retry")
def retry_stage_endpoint(stage: str, payload: dict = Body(default={})) -> dict:
    from .deployment_workflow import get_deployment_workflow_engine

    execution_id = payload.get("execution_id")
    if not execution_id:
        raise HTTPException(status_code=422, detail="execution_id is required")
    timeout_seconds = payload.get("timeout_seconds", 30.0)

    try:
        result = get_deployment_stage_orchestrator().retry_stage(
            execution_id,
            stage,
            action=_build_action(payload),
            timeout_seconds=timeout_seconds,
            workflow_engine=get_deployment_workflow_engine(),
        )
    except UnknownExecutionError:
        raise HTTPException(status_code=404, detail="unknown execution")
    except UnknownStageError:
        raise HTTPException(status_code=404, detail="unknown stage")
    except (InvalidRetryError, RetryLimitExceededError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return result.to_dict()


@router.get("/stages/{stage}")
def get_stage_endpoint(stage: str) -> dict:
    try:
        execution = get_deployment_stage_orchestrator().get_stage(stage)
    except UnknownStageError:
        raise HTTPException(status_code=404, detail="no result recorded for stage")
    return execution.to_dict()
