from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .job_registry import JobRegistry, JobStatus, UnknownJobError, get_job_registry


class EmptyQueueError(LookupError):
    pass


@dataclass(frozen=True)
class QueuedJob:
    """A job waiting in the dispatch queue."""

    job_id: str
    priority: int
    enqueued_at: datetime
    available_at: datetime

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "priority": self.priority,
            "enqueued_at": self.enqueued_at.isoformat(),
            "available_at": self.available_at.isoformat(),
        }


@dataclass(frozen=True)
class QueueState:
    """A snapshot of queue statistics."""

    size: int
    ready: int
    delayed: int

    def to_dict(self) -> dict:
        return {
            "size": self.size,
            "ready": self.ready,
            "delayed": self.delayed,
        }


def _sort_key(queued_job: QueuedJob):
    return (-queued_job.priority, queued_job.enqueued_at)


class JobQueueManager:
    """Buffers registered jobs for later dispatch, in FIFO-within-priority order."""

    def __init__(self) -> None:
        self._items: list = []
        self._lock = Lock()

    def enqueue(
        self,
        job_id: str,
        *,
        priority: int = 0,
        delay_seconds: float = 0.0,
        registry: Optional[JobRegistry] = None,
    ) -> QueuedJob:
        if not job_id:
            raise ValueError("job id is required")
        if delay_seconds < 0:
            raise ValueError("delay_seconds must not be negative")
        if registry is not None and not registry.is_registered(job_id):
            raise UnknownJobError(job_id)
        now = datetime.now(timezone.utc)
        queued_job = QueuedJob(
            job_id=job_id,
            priority=priority,
            enqueued_at=now,
            available_at=now + timedelta(seconds=delay_seconds),
        )
        with self._lock:
            self._items.append(queued_job)
        return queued_job

    def dequeue(self, *, registry: Optional[JobRegistry] = None) -> QueuedJob:
        now = datetime.now(timezone.utc)
        with self._lock:
            ready = [item for item in self._items if item.available_at <= now]
            if not ready:
                raise EmptyQueueError("no ready jobs in queue")
            chosen = min(ready, key=_sort_key)
            self._items.remove(chosen)
        if registry is not None:
            registry.update_status(chosen.job_id, JobStatus.RUNNING)
        return chosen

    def peek(self) -> QueuedJob:
        now = datetime.now(timezone.utc)
        with self._lock:
            ready = [item for item in self._items if item.available_at <= now]
            if not ready:
                raise EmptyQueueError("no ready jobs in queue")
            return min(ready, key=_sort_key)

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count

    def list_queued(self) -> list:
        with self._lock:
            return sorted(self._items, key=_sort_key)

    def stats(self) -> QueueState:
        now = datetime.now(timezone.utc)
        with self._lock:
            size = len(self._items)
            delayed = sum(1 for item in self._items if item.available_at > now)
            return QueueState(size=size, ready=size - delayed, delayed=delayed)


_job_queue_manager = JobQueueManager()


def get_job_queue_manager() -> JobQueueManager:
    return _job_queue_manager


router = APIRouter(prefix="/jobs/queue", tags=["job-queue"])


@router.post("", status_code=201)
def enqueue_job_endpoint(
    payload: dict = Body(default={}),
    queue: JobQueueManager = Depends(get_job_queue_manager),
    registry: JobRegistry = Depends(get_job_registry),
) -> dict:
    try:
        queued_job = queue.enqueue(
            payload.get("job_id", ""),
            priority=payload.get("priority", 0),
            delay_seconds=payload.get("delay_seconds", 0.0),
            registry=registry,
        )
    except UnknownJobError:
        raise HTTPException(status_code=404, detail="unknown job")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return queued_job.to_dict()


@router.get("")
def list_queue_endpoint(
    queue: JobQueueManager = Depends(get_job_queue_manager),
) -> dict:
    return {
        "jobs": [item.to_dict() for item in queue.list_queued()],
        "state": queue.stats().to_dict(),
    }


@router.get("/next")
def peek_queue_endpoint(
    queue: JobQueueManager = Depends(get_job_queue_manager),
) -> dict:
    try:
        return queue.peek().to_dict()
    except EmptyQueueError:
        raise HTTPException(status_code=404, detail="queue is empty")


@router.delete("", status_code=204)
def clear_queue_endpoint(
    queue: JobQueueManager = Depends(get_job_queue_manager),
) -> None:
    queue.clear()
