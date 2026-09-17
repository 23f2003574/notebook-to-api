from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .execution_coordinator import ExecutionCoordinator, FAILED, get_execution_coordinator
from .job_dispatcher import DistributedJobDispatcher, get_job_dispatcher
from .worker_discovery import WorkerDiscoveryService, get_worker_discovery_service
from .worker_registry import WorkerRegistry, get_worker_registry


@dataclass(frozen=True)
class FailureEvent:
    """An observed or reported failure affecting an execution."""

    execution_id: str
    worker_id: Optional[str]
    failure_type: str
    detail: Optional[str]
    detected_at: datetime

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "worker_id": self.worker_id,
            "failure_type": self.failure_type,
            "detail": self.detail,
            "detected_at": self.detected_at.isoformat(),
        }


@dataclass(frozen=True)
class RecoveryPlan:
    """The strategy chosen (and executed) to recover a failed execution."""

    execution_id: str
    strategy: str
    action_taken: str
    new_worker_id: Optional[str]
    attempt: int
    recovered_at: datetime
    success: bool

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "strategy": self.strategy,
            "action_taken": self.action_taken,
            "new_worker_id": self.new_worker_id,
            "attempt": self.attempt,
            "recovered_at": self.recovered_at.isoformat(),
            "success": self.success,
        }


class FaultToleranceManager:
    """Detects execution/worker failures and recovers via retry, failover, or reassignment."""

    def __init__(
        self,
        coordinator: ExecutionCoordinator,
        dispatcher: DistributedJobDispatcher,
        registry: WorkerRegistry,
        discovery: WorkerDiscoveryService,
    ) -> None:
        self._coordinator = coordinator
        self._dispatcher = dispatcher
        self._registry = registry
        self._discovery = discovery
        self._events: list = []
        self._plans: list = []
        self._lock = Lock()

    def detect_failure(
        self,
        execution_id: str,
        *,
        failure_type: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> FailureEvent:
        session = self._coordinator.get_session(execution_id)
        if session is None:
            raise KeyError(execution_id)

        if failure_type is None:
            failure_type = self._classify(session)

        event = FailureEvent(
            execution_id=execution_id,
            worker_id=session.worker_id,
            failure_type=failure_type,
            detail=detail,
            detected_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._events.append(event)
        return event

    def _classify(self, session) -> str:
        if session.state == FAILED:
            return "execution_failed"
        if session.worker_id is not None:
            node = self._registry.get(session.worker_id)
            if node is None or node.status == "offline":
                return "worker_offline"
            if self._discovery.get_health(session.worker_id) == "unhealthy":
                return "worker_unhealthy"
        return "unknown"

    def recover(
        self,
        execution_id: str,
        *,
        failure_type: Optional[str] = None,
        checkpoint: Optional[dict] = None,
    ) -> RecoveryPlan:
        detail = f"checkpoint at progress {checkpoint.get('progress')}" if checkpoint else None
        event = self.detect_failure(execution_id, failure_type=failure_type, detail=detail)

        if checkpoint is not None:
            return self._resubmit(execution_id, strategy="checkpoint_restore")
        if event.failure_type in ("worker_unhealthy", "worker_offline"):
            return self._reassign(execution_id, strategy="worker_failover")
        return self.retry(execution_id)

    def retry(self, execution_id: str) -> RecoveryPlan:
        return self._resubmit(execution_id, strategy="retry")

    def reassign(self, execution_id: str) -> RecoveryPlan:
        return self._reassign(execution_id, strategy="task_reassignment")

    def list_events(self, *, execution_id: Optional[str] = None) -> list:
        with self._lock:
            events = list(self._events)
        if execution_id is not None:
            events = [event for event in events if event.execution_id == execution_id]
        return events

    def _resubmit(self, execution_id: str, *, strategy: str) -> RecoveryPlan:
        session = self._coordinator.get_session(execution_id)
        if session is None:
            raise KeyError(execution_id)
        if session.state != FAILED:
            raise ValueError(f"execution '{execution_id}' is not in a failed state; cannot {strategy}")

        task = self._dispatcher.get_serialized_task(execution_id)
        new_session = self._coordinator.submit(
            execution_id,
            session.capability,
            priority=task.priority if task is not None else 0,
            policy=task.policy if task is not None else "least_loaded",
            payload=task.payload if task is not None else None,
            format=task.metadata.format if task is not None else "json",
        )
        return self._record_plan(
            execution_id,
            strategy=strategy,
            action_taken=f"resubmitted as attempt {new_session.attempt}",
            new_worker_id=new_session.worker_id,
            attempt=new_session.attempt,
        )

    def _reassign(self, execution_id: str, *, strategy: str) -> RecoveryPlan:
        session = self._coordinator.reassign(execution_id)
        action_taken = (
            f"reassigned to '{session.worker_id}'" if session.worker_id else "no available worker; job re-queued"
        )
        return self._record_plan(
            execution_id,
            strategy=strategy,
            action_taken=action_taken,
            new_worker_id=session.worker_id,
            attempt=session.attempt,
        )

    def _record_plan(
        self,
        execution_id: str,
        *,
        strategy: str,
        action_taken: str,
        new_worker_id: Optional[str],
        attempt: int,
    ) -> RecoveryPlan:
        plan = RecoveryPlan(
            execution_id=execution_id,
            strategy=strategy,
            action_taken=action_taken,
            new_worker_id=new_worker_id,
            attempt=attempt,
            recovered_at=datetime.now(timezone.utc),
            success=new_worker_id is not None,
        )
        with self._lock:
            self._plans.append(plan)
        return plan


_fault_tolerance_manager = FaultToleranceManager(
    get_execution_coordinator(), get_job_dispatcher(), get_worker_registry(), get_worker_discovery_service()
)


def get_fault_tolerance_manager() -> FaultToleranceManager:
    return _fault_tolerance_manager


router = APIRouter(prefix="/cluster/recovery", tags=["fault-tolerance"])


@router.post("")
def recover_endpoint(
    payload: dict,
    manager: FaultToleranceManager = Depends(get_fault_tolerance_manager),
) -> dict:
    try:
        plan = manager.recover(
            payload["execution_id"],
            failure_type=payload.get("failure_type"),
            checkpoint=payload.get("checkpoint"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return plan.to_dict()


@router.post("/retry")
def retry_endpoint(
    payload: dict,
    manager: FaultToleranceManager = Depends(get_fault_tolerance_manager),
) -> dict:
    try:
        plan = manager.retry(payload["execution_id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return plan.to_dict()


@router.post("/reassign")
def reassign_endpoint(
    payload: dict,
    manager: FaultToleranceManager = Depends(get_fault_tolerance_manager),
) -> dict:
    try:
        plan = manager.reassign(payload["execution_id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return plan.to_dict()


@router.get("/events")
def list_events_endpoint(
    execution_id: Optional[str] = None,
    manager: FaultToleranceManager = Depends(get_fault_tolerance_manager),
) -> list:
    return [event.to_dict() for event in manager.list_events(execution_id=execution_id)]
