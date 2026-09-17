from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .task_serializer import SerializedTask, TaskSerializationEngine, get_task_serialization_engine
from .worker_discovery import WorkerDiscoveryService, get_worker_discovery_service

_VALID_POLICIES = {"round_robin", "least_loaded", "capability_match", "priority"}


@dataclass(frozen=True)
class DispatchRequest:
    """A request to run a job on a worker with the given capability."""

    job_id: str
    capability: str
    priority: int = 0
    policy: str = "least_loaded"

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "capability": self.capability,
            "priority": self.priority,
            "policy": self.policy,
        }


@dataclass(frozen=True)
class DispatchResult:
    """The outcome of attempting to dispatch a job."""

    job_id: str
    worker_id: Optional[str]
    status: str
    assigned_at: Optional[datetime]
    reason: Optional[str]

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "worker_id": self.worker_id,
            "status": self.status,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "reason": self.reason,
        }


class DistributedJobDispatcher:
    """Assigns jobs to worker nodes using a pluggable selection policy, queuing what it can't place."""

    def __init__(
        self,
        discovery: WorkerDiscoveryService,
        *,
        default_policy: str = "least_loaded",
        serializer: Optional[TaskSerializationEngine] = None,
    ) -> None:
        self._discovery = discovery
        self._default_policy = default_policy
        self._serializer = serializer or get_task_serialization_engine()
        self._dispatches: dict = {}
        self._requests: dict = {}
        self._serialized: dict = {}
        self._queue: list = []
        self._seq = 0
        self._round_robin_cursor: dict = {}
        self._lock = Lock()

    def select_worker(self, request: DispatchRequest):
        policy = request.policy or self._default_policy
        if policy not in _VALID_POLICIES:
            raise ValueError(f"unsupported dispatch policy '{policy}'; expected one of {sorted(_VALID_POLICIES)}")

        if policy == "round_robin":
            candidates = self._discovery.available_workers(capability=request.capability)
            return self._select_round_robin(request.capability, candidates)
        if policy == "capability_match":
            candidates = self._discovery.available_workers(capability=request.capability)
            return sorted(candidates, key=lambda worker: worker.worker_id)[0] if candidates else None
        # "least_loaded" and "priority" both place the job on the least-busy eligible worker;
        # priority instead affects queue ordering when no worker is available yet.
        return self._discovery.least_loaded(capability=request.capability)

    def _select_round_robin(self, capability: str, candidates: list):
        if not candidates:
            return None
        candidates = sorted(candidates, key=lambda worker: worker.worker_id)
        index = self._round_robin_cursor.get(capability, 0) % len(candidates)
        self._round_robin_cursor[capability] = index + 1
        return candidates[index]

    def dispatch(
        self,
        request: DispatchRequest,
        *,
        payload: Optional[dict] = None,
        format: str = "json",
    ) -> DispatchResult:
        if not request.job_id:
            raise ValueError("job_id must be non-empty")
        if not request.capability:
            raise ValueError("capability must be non-empty")

        task = self._serializer.serialize(
            request.job_id,
            request.capability,
            payload,
            priority=request.priority,
            policy=request.policy,
            format=format,
        )

        with self._lock:
            self._requests[request.job_id] = request
            self._serialized[request.job_id] = task
            worker = self.select_worker(request)
            if worker is None:
                self._enqueue(request.job_id)
                result = DispatchResult(
                    job_id=request.job_id,
                    worker_id=None,
                    status="queued",
                    assigned_at=None,
                    reason="no available worker with required capability",
                )
            else:
                self._discovery.increment_load(worker.worker_id)
                result = DispatchResult(
                    job_id=request.job_id,
                    worker_id=worker.worker_id,
                    status="dispatched",
                    assigned_at=datetime.now(timezone.utc),
                    reason=None,
                )
            self._dispatches[request.job_id] = result
            return result

    def reassign(self, job_id: str) -> DispatchResult:
        with self._lock:
            request = self._requests.get(job_id)
            previous = self._dispatches.get(job_id)
            if request is None or previous is None:
                raise KeyError(job_id)

            if previous.worker_id is not None:
                self._discovery.decrement_load(previous.worker_id)
            self._remove_from_queue(job_id)

            worker = self.select_worker(request)
            if worker is None:
                self._enqueue(job_id)
                result = DispatchResult(
                    job_id=job_id,
                    worker_id=None,
                    status="queued",
                    assigned_at=None,
                    reason="no available worker with required capability",
                )
            else:
                self._discovery.increment_load(worker.worker_id)
                result = DispatchResult(
                    job_id=job_id,
                    worker_id=worker.worker_id,
                    status="dispatched",
                    assigned_at=datetime.now(timezone.utc),
                    reason=None,
                )
            self._dispatches[job_id] = result
            return result

    def cancel(self, job_id: str) -> bool:
        """Abandon a job: release any held worker load and discard its dispatch history."""
        with self._lock:
            previous = self._dispatches.get(job_id)
            if previous is None:
                return False
            if previous.worker_id is not None:
                self._discovery.decrement_load(previous.worker_id)
            self._remove_from_queue(job_id)
            self._dispatches.pop(job_id, None)
            self._requests.pop(job_id, None)
            self._serialized.pop(job_id, None)
            return True

    def release(self, job_id: str) -> bool:
        """Free the worker slot held by a finished job, keeping its dispatch history intact."""
        with self._lock:
            previous = self._dispatches.get(job_id)
            if previous is None or previous.worker_id is None:
                return False
            self._discovery.decrement_load(previous.worker_id)
            return True

    def queue_status(self) -> list:
        with self._lock:
            queued = list(self._queue)
        statuses = []
        for position, (_, job_id) in enumerate(queued):
            request = self._requests[job_id]
            statuses.append(
                {
                    "job_id": job_id,
                    "capability": request.capability,
                    "priority": request.priority,
                    "policy": request.policy,
                    "position": position,
                }
            )
        return statuses

    def get_dispatch(self, job_id: str) -> Optional[DispatchResult]:
        return self._dispatches.get(job_id)

    def get_serialized_task(self, job_id: str) -> Optional[SerializedTask]:
        return self._serialized.get(job_id)

    def _enqueue(self, job_id: str) -> None:
        self._seq += 1
        self._queue.append((self._seq, job_id))
        self._queue.sort(key=lambda item: (-self._requests[item[1]].priority, item[0]))

    def _remove_from_queue(self, job_id: str) -> None:
        self._queue = [item for item in self._queue if item[1] != job_id]


_job_dispatcher = DistributedJobDispatcher(get_worker_discovery_service())


def get_job_dispatcher() -> DistributedJobDispatcher:
    return _job_dispatcher


router = APIRouter(prefix="/cluster/dispatch", tags=["job-dispatcher"])


@router.post("")
def dispatch_endpoint(
    payload: dict,
    dispatcher: DistributedJobDispatcher = Depends(get_job_dispatcher),
) -> dict:
    try:
        request = DispatchRequest(
            job_id=payload["job_id"],
            capability=payload["capability"],
            priority=payload.get("priority", 0),
            policy=payload.get("policy", "least_loaded"),
        )
        result = dispatcher.dispatch(
            request,
            payload=payload.get("payload"),
            format=payload.get("format", "json"),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.get("/queue")
def queue_status_endpoint(
    dispatcher: DistributedJobDispatcher = Depends(get_job_dispatcher),
) -> list:
    return dispatcher.queue_status()


@router.get("/{job_id}")
def get_dispatch_endpoint(
    job_id: str,
    dispatcher: DistributedJobDispatcher = Depends(get_job_dispatcher),
) -> dict:
    result = dispatcher.get_dispatch(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    return result.to_dict()


@router.post("/reassign")
def reassign_endpoint(
    payload: dict,
    dispatcher: DistributedJobDispatcher = Depends(get_job_dispatcher),
) -> dict:
    job_id = payload.get("job_id")
    if not job_id:
        raise HTTPException(status_code=422, detail="job_id is required")
    try:
        result = dispatcher.reassign(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    return result.to_dict()
