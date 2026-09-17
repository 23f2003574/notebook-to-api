from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .deployment_recovery import DeploymentRecoveryCoordinator

EXPERIMENT_STATUSES = ("RUNNING", "COMPLETED", "STOPPED", "FAILED")


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownExperimentError(KeyError):
    pass


class UnknownExecutionError(KeyError):
    pass


class ChaosExperiment:
    """Common interface every chaos experiment must implement."""

    def __init__(self, name: str) -> None:
        self.name = name

    def inject(self, context: Mapping[str, Any]) -> str:
        """Apply the fault. Return a description of what was injected."""

        raise NotImplementedError

    def cleanup(self, context: Mapping[str, Any]) -> str:
        """Revert the fault. Return a description of the cleanup performed."""

        raise NotImplementedError


class DeploymentFailureExperiment(ChaosExperiment):
    def __init__(self) -> None:
        super().__init__("deployment_failure")

    def inject(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"forced deployment {deployment} to fail"

    def cleanup(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"restored deployment {deployment} to a healthy state"


class NetworkLatencyExperiment(ChaosExperiment):
    def __init__(self) -> None:
        super().__init__("network_latency")

    def inject(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        latency_ms = context.get("latency_ms", 500)
        return f"injected {latency_ms}ms of network latency into {deployment}"

    def cleanup(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"removed injected network latency from {deployment}"


class ServiceTimeoutExperiment(ChaosExperiment):
    def __init__(self) -> None:
        super().__init__("service_timeout")

    def inject(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"forced service timeouts on {deployment}"

    def cleanup(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"cleared forced timeouts on {deployment}"


class NodeUnavailableExperiment(ChaosExperiment):
    def __init__(self) -> None:
        super().__init__("node_unavailable")

    def inject(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"marked the node backing {deployment} unavailable"

    def cleanup(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        return f"restored node availability for {deployment}"


class ResourceExhaustionExperiment(ChaosExperiment):
    def __init__(self) -> None:
        super().__init__("resource_exhaustion")

    def inject(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        resource = context.get("resource", "cpu")
        return f"exhausted {resource} on {deployment}"

    def cleanup(self, context: Mapping[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        resource = context.get("resource", "cpu")
        return f"released exhausted {resource} on {deployment}"


DEFAULT_EXPERIMENTS: tuple[ChaosExperiment, ...] = (
    DeploymentFailureExperiment(),
    NetworkLatencyExperiment(),
    ServiceTimeoutExperiment(),
    NodeUnavailableExperiment(),
    ResourceExhaustionExperiment(),
)


class TimerHandle:
    """Common interface for a cancellable, delayed callback."""

    def cancel(self) -> None:
        raise NotImplementedError


class _ThreadingTimerHandle(TimerHandle):
    def __init__(self, timer: threading.Timer) -> None:
        self._timer = timer

    def cancel(self) -> None:
        self._timer.cancel()


def _default_scheduler(delay: float, callback: Callable[[], None]) -> TimerHandle:
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return _ThreadingTimerHandle(timer)


@dataclass(frozen=True)
class ExperimentRecord:
    """One immutable record of a chaos experiment execution."""

    execution_id: str
    experiment: str
    deployment: str
    status: str
    injected_message: Optional[str]
    cleanup_message: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    duration_seconds: float
    recovery_id: Optional[str] = None
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "experiment": self.experiment,
            "deployment": self.deployment,
            "status": self.status,
            "injected_message": self.injected_message,
            "cleanup_message": self.cleanup_message,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": self.duration_seconds,
            "recovery_id": self.recovery_id,
            "context": dict(self.context),
        }


class DeploymentChaosFramework:
    """Injects controlled deployment failures to validate resilience."""

    def __init__(self, experiments: Optional[Iterable[ChaosExperiment]] = None) -> None:
        self._experiments: dict[str, ChaosExperiment] = {}
        self._active: dict[str, ExperimentRecord] = {}
        self._handles: dict[str, TimerHandle] = {}
        self._history: list[ExperimentRecord] = []
        self._lock = Lock()
        for experiment in (
            DEFAULT_EXPERIMENTS if experiments is None else experiments
        ):
            self.register_experiment(experiment)

    def register_experiment(self, experiment: ChaosExperiment) -> None:
        with self._lock:
            self._experiments[experiment.name] = experiment

    def run(
        self,
        experiment_name: str,
        context: Mapping[str, Any],
        *,
        duration_seconds: float = 1.0,
        timestamp: Optional[datetime] = None,
        scheduler: Callable[[float, Callable[[], None]], TimerHandle] = _default_scheduler,
        recovery_coordinator: Optional[DeploymentRecoveryCoordinator] = None,
    ) -> ExperimentRecord:
        experiment = self._experiments.get(experiment_name)
        if experiment is None:
            raise UnknownExperimentError(experiment_name)
        deployment = context.get("deployment")
        if not deployment:
            raise ValueError("context must provide a 'deployment' identifier")

        started_at = timestamp or datetime.now(timezone.utc)
        try:
            injected_message = experiment.inject(context)
        except Exception as exc:  # noqa: BLE001 - experiment failures are data
            record = ExperimentRecord(
                execution_id=_new_id(),
                experiment=experiment.name,
                deployment=deployment,
                status="FAILED",
                injected_message=None,
                cleanup_message=None,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc),
                duration_seconds=duration_seconds,
                context=dict(context),
            )
            with self._lock:
                self._history.append(record)
            return record

        recovery_id = None
        if recovery_coordinator is not None:
            recovery_record = recovery_coordinator.recover(
                context, timestamp=timestamp, source="chaos"
            )
            recovery_id = recovery_record.recovery_id

        record = ExperimentRecord(
            execution_id=_new_id(),
            experiment=experiment.name,
            deployment=deployment,
            status="RUNNING",
            injected_message=injected_message,
            cleanup_message=None,
            started_at=started_at,
            ended_at=None,
            duration_seconds=duration_seconds,
            recovery_id=recovery_id,
            context=dict(context),
        )
        with self._lock:
            self._active[record.execution_id] = record
            self._handles[record.execution_id] = scheduler(
                duration_seconds, lambda: self._finish(record.execution_id, "COMPLETED")
            )
        return record

    def stop(self, execution_id: str) -> ExperimentRecord:
        with self._lock:
            handle = self._handles.pop(execution_id, None)
        if handle is not None:
            handle.cancel()
        return self._finish(execution_id, "STOPPED")

    def history(self, deployment: Optional[str] = None) -> list[ExperimentRecord]:
        with self._lock:
            records = list(self._history)
        if deployment is not None:
            records = [r for r in records if r.deployment == deployment]
        return records

    def _finish(self, execution_id: str, status: str) -> ExperimentRecord:
        with self._lock:
            record = self._active.pop(execution_id, None)
            self._handles.pop(execution_id, None)
            if record is None:
                existing = next(
                    (r for r in reversed(self._history) if r.execution_id == execution_id),
                    None,
                )
                if existing is not None:
                    return existing
                raise UnknownExecutionError(execution_id)
            experiment = self._experiments[record.experiment]

        try:
            cleanup_message = experiment.cleanup(record.context)
        except Exception as exc:  # noqa: BLE001 - cleanup failures are data
            cleanup_message = f"cleanup failed: {exc}"
            status = "FAILED"

        finished = ExperimentRecord(
            execution_id=record.execution_id,
            experiment=record.experiment,
            deployment=record.deployment,
            status=status,
            injected_message=record.injected_message,
            cleanup_message=cleanup_message,
            started_at=record.started_at,
            ended_at=datetime.now(timezone.utc),
            duration_seconds=record.duration_seconds,
            recovery_id=record.recovery_id,
            context=record.context,
        )
        with self._lock:
            self._history.append(finished)
        return finished


_framework = DeploymentChaosFramework()


def get_deployment_chaos_framework() -> DeploymentChaosFramework:
    return _framework


router = APIRouter(prefix="/governance", tags=["governance-chaos"])


@router.post("/chaos/run")
def run_experiment(payload: dict = Body(...)) -> dict:
    experiment_name = payload.get("experiment")
    deployment = payload.get("deployment")
    if not experiment_name or not deployment:
        raise HTTPException(
            status_code=422, detail="experiment and deployment are required"
        )
    duration_seconds = payload.get("duration_seconds", 1.0)
    try:
        record = get_deployment_chaos_framework().run(
            experiment_name, payload, duration_seconds=duration_seconds
        )
    except UnknownExperimentError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown experiment: {exc.args[0]}"
        )
    return record.to_dict()


@router.post("/chaos/stop")
def stop_experiment(payload: dict = Body(...)) -> dict:
    execution_id = payload.get("execution_id")
    if not execution_id:
        raise HTTPException(status_code=422, detail="execution_id is required")
    try:
        record = get_deployment_chaos_framework().stop(execution_id)
    except UnknownExecutionError:
        raise HTTPException(status_code=404, detail="unknown execution_id")
    return record.to_dict()


@router.get("/chaos/history")
def chaos_history(deployment: Optional[str] = Query(default=None)) -> list[dict]:
    records = get_deployment_chaos_framework().history(deployment=deployment)
    return [record.to_dict() for record in records]
