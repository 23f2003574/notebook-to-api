from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .job_dispatcher import DispatchRequest, DistributedJobDispatcher, get_job_dispatcher

QUEUED = "queued"
ASSIGNED = "assigned"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

_TERMINAL_STATES = {COMPLETED, FAILED, CANCELLED}
_PROGRESS_BY_STATE = {
    QUEUED: 0.0,
    ASSIGNED: 0.25,
    RUNNING: 0.6,
    COMPLETED: 1.0,
    FAILED: 1.0,
    CANCELLED: 1.0,
}


@dataclass(frozen=True)
class ExecutionState:
    """A single state transition recorded in an execution's history."""

    state: str
    occurred_at: datetime
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "occurred_at": self.occurred_at.isoformat(),
            "detail": self.detail,
        }


@dataclass
class ExecutionSession:
    """The distributed lifecycle of a single submitted job."""

    execution_id: str
    job_id: str
    capability: str
    worker_id: Optional[str]
    state: str
    progress: float = 0.0
    result: Optional[dict] = None
    error: Optional[str] = None
    history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "job_id": self.job_id,
            "capability": self.capability,
            "worker_id": self.worker_id,
            "state": self.state,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "history": [event.to_dict() for event in self.history],
        }


class ExecutionCoordinator:
    """Coordinates a job's distributed lifecycle on top of the dispatcher: queued -> ... -> terminal."""

    def __init__(self, dispatcher: DistributedJobDispatcher) -> None:
        self._dispatcher = dispatcher
        self._sessions: dict = {}
        self._lock = Lock()

    def submit(
        self,
        job_id: str,
        capability: str,
        *,
        priority: int = 0,
        policy: str = "least_loaded",
        payload: Optional[dict] = None,
        format: str = "json",
    ) -> ExecutionSession:
        if not job_id:
            raise ValueError("job_id must be non-empty")
        if not capability:
            raise ValueError("capability must be non-empty")

        with self._lock:
            existing = self._sessions.get(job_id)
            if existing is not None and existing.state not in _TERMINAL_STATES:
                raise ValueError(f"execution '{job_id}' is already in progress")

        request = DispatchRequest(job_id=job_id, capability=capability, priority=priority, policy=policy)
        result = self._dispatcher.dispatch(request, payload=payload, format=format)
        state = ASSIGNED if result.worker_id else QUEUED
        session = ExecutionSession(
            execution_id=job_id,
            job_id=job_id,
            capability=capability,
            worker_id=result.worker_id,
            state=state,
            progress=_PROGRESS_BY_STATE[state],
            history=[ExecutionState(state=state, occurred_at=datetime.now(timezone.utc), detail=result.reason)],
        )
        with self._lock:
            self._sessions[job_id] = session
        return session

    def monitor(self, execution_id: str) -> ExecutionSession:
        with self._lock:
            session = self._sessions.get(execution_id)
            if session is None:
                raise KeyError(execution_id)
            if session.state in _TERMINAL_STATES:
                return session

            dispatch = self._dispatcher.get_dispatch(execution_id)
            if session.state == QUEUED and dispatch is not None and dispatch.worker_id is not None:
                self._transition(session, ASSIGNED, worker_id=dispatch.worker_id, detail=f"assigned to {dispatch.worker_id}")
            elif session.state == ASSIGNED:
                self._transition(session, RUNNING, detail="execution started")
            return session

    def complete(
        self,
        execution_id: str,
        *,
        success: bool = True,
        result: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> ExecutionSession:
        with self._lock:
            session = self._sessions.get(execution_id)
            if session is None:
                raise KeyError(execution_id)
            if session.state in _TERMINAL_STATES:
                raise ValueError(f"execution '{execution_id}' is already in terminal state '{session.state}'")

            self._dispatcher.release(execution_id)
            if success:
                session.result = result
                self._transition(session, COMPLETED, detail="execution completed")
            else:
                session.error = error
                self._transition(session, FAILED, detail=error or "execution failed")
            return session

    def cancel(self, execution_id: str) -> ExecutionSession:
        with self._lock:
            session = self._sessions.get(execution_id)
            if session is None:
                raise KeyError(execution_id)
            if session.state in _TERMINAL_STATES:
                raise ValueError(f"execution '{execution_id}' is already in terminal state '{session.state}'")

            self._dispatcher.cancel(execution_id)
            self._transition(session, CANCELLED, detail="execution cancelled")
            return session

    def list_executions(self, *, state: Optional[str] = None) -> list:
        with self._lock:
            sessions = list(self._sessions.values())
        if state is not None:
            sessions = [session for session in sessions if session.state == state]
        return sorted(sessions, key=lambda session: session.execution_id)

    def _transition(
        self,
        session: ExecutionSession,
        new_state: str,
        *,
        worker_id: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        session.state = new_state
        if worker_id is not None:
            session.worker_id = worker_id
        session.progress = _PROGRESS_BY_STATE[new_state]
        session.history.append(ExecutionState(state=new_state, occurred_at=datetime.now(timezone.utc), detail=detail))


_execution_coordinator = ExecutionCoordinator(get_job_dispatcher())


def get_execution_coordinator() -> ExecutionCoordinator:
    return _execution_coordinator


router = APIRouter(prefix="/cluster/executions", tags=["execution-coordinator"])


@router.post("")
def submit_endpoint(
    payload: dict,
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
) -> dict:
    try:
        session = coordinator.submit(
            job_id=payload["job_id"],
            capability=payload["capability"],
            priority=payload.get("priority", 0),
            policy=payload.get("policy", "least_loaded"),
            payload=payload.get("payload"),
            format=payload.get("format", "json"),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return session.to_dict()


@router.get("")
def list_executions_endpoint(
    state: Optional[str] = None,
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
) -> list:
    return [session.to_dict() for session in coordinator.list_executions(state=state)]


@router.get("/{execution_id}")
def monitor_endpoint(
    execution_id: str,
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
) -> dict:
    try:
        session = coordinator.monitor(execution_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"execution '{execution_id}' not found")
    return session.to_dict()


@router.delete("/{execution_id}")
def cancel_endpoint(
    execution_id: str,
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
) -> dict:
    try:
        session = coordinator.cancel(execution_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"execution '{execution_id}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return session.to_dict()
