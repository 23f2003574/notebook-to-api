from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .worker_registry import WorkerRegistry, get_worker_registry


@dataclass(frozen=True)
class DiscoveryRecord:
    """A worker's discovery status at a point in time."""

    worker_id: str
    capabilities: tuple
    status: str
    is_stale: bool
    seconds_since_heartbeat: float
    active_jobs: int = 0

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "capabilities": list(self.capabilities),
            "status": self.status,
            "is_stale": self.is_stale,
            "seconds_since_heartbeat": self.seconds_since_heartbeat,
            "active_jobs": self.active_jobs,
        }


@dataclass(frozen=True)
class HeartbeatStatus:
    """The result of a worker checking in with the discovery service."""

    worker_id: str
    received_at: datetime
    status: str
    active_jobs: int = 0

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "received_at": self.received_at.isoformat(),
            "status": self.status,
            "active_jobs": self.active_jobs,
        }


class WorkerDiscoveryService:
    """Discovers registered workers and monitors their liveness via heartbeats."""

    def __init__(self, registry: WorkerRegistry, *, stale_after_seconds: float = 30.0) -> None:
        self._registry = registry
        self._stale_after_seconds = stale_after_seconds
        self._load: dict = {}

    def heartbeat(self, worker_id: str, *, active_jobs: Optional[int] = None) -> HeartbeatStatus:
        node = self._registry.touch(worker_id)
        if active_jobs is not None:
            self.set_load(worker_id, active_jobs)
        return HeartbeatStatus(
            worker_id=worker_id,
            received_at=node.last_seen_at,
            status=node.status,
            active_jobs=self.get_load(worker_id),
        )

    def discover(self, *, capability: Optional[str] = None) -> list:
        records = []
        for node in self._registry.list_workers(capability=capability):
            elapsed = self._seconds_since_heartbeat(node.last_seen_at)
            records.append(
                DiscoveryRecord(
                    worker_id=node.worker_id,
                    capabilities=node.capabilities,
                    status=node.status,
                    is_stale=elapsed > self._stale_after_seconds,
                    seconds_since_heartbeat=elapsed,
                    active_jobs=self.get_load(node.worker_id),
                )
            )
        return records

    def get_load(self, worker_id: str) -> int:
        return self._load.get(worker_id, 0)

    def set_load(self, worker_id: str, active_jobs: int) -> None:
        self._load[worker_id] = max(0, active_jobs)

    def increment_load(self, worker_id: str, amount: int = 1) -> int:
        self._load[worker_id] = self.get_load(worker_id) + amount
        return self._load[worker_id]

    def decrement_load(self, worker_id: str, amount: int = 1) -> int:
        self._load[worker_id] = max(0, self.get_load(worker_id) - amount)
        return self._load[worker_id]

    def least_loaded(self, *, capability: Optional[str] = None) -> Optional[object]:
        """The available worker with the fewest active jobs, or None if none are available."""
        candidates = self.available_workers(capability=capability)
        if not candidates:
            return None
        return min(candidates, key=lambda worker: (self.get_load(worker.worker_id), worker.worker_id))

    def refresh(self) -> list:
        """Mark workers that have gone quiet for too long as offline. Returns the ids marked stale."""
        marked = []
        for node in self._registry.list_workers():
            if node.status == "offline":
                continue
            if self._seconds_since_heartbeat(node.last_seen_at) > self._stale_after_seconds:
                self._registry.set_status(node.worker_id, "offline")
                marked.append(node.worker_id)
        return marked

    def available_workers(self, *, capability: Optional[str] = None) -> list:
        self.refresh()
        return self._registry.list_workers(status="online", capability=capability)

    def _seconds_since_heartbeat(self, last_seen_at: datetime) -> float:
        return (datetime.now(timezone.utc) - last_seen_at).total_seconds()


_worker_discovery_service = WorkerDiscoveryService(get_worker_registry())


def get_worker_discovery_service() -> WorkerDiscoveryService:
    return _worker_discovery_service


router = APIRouter(prefix="/cluster/discovery", tags=["worker-discovery"])


@router.post("/heartbeat")
def heartbeat_endpoint(
    payload: dict,
    service: WorkerDiscoveryService = Depends(get_worker_discovery_service),
) -> dict:
    worker_id = payload.get("worker_id")
    if not worker_id:
        raise HTTPException(status_code=422, detail="worker_id is required")
    try:
        status = service.heartbeat(worker_id, active_jobs=payload.get("active_jobs"))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"worker '{worker_id}' not found")
    return status.to_dict()


@router.get("")
def discover_endpoint(
    capability: Optional[str] = None,
    service: WorkerDiscoveryService = Depends(get_worker_discovery_service),
) -> list:
    return [record.to_dict() for record in service.discover(capability=capability)]


@router.get("/available")
def available_workers_endpoint(
    capability: Optional[str] = None,
    service: WorkerDiscoveryService = Depends(get_worker_discovery_service),
) -> list:
    return [worker.to_dict() for worker in service.available_workers(capability=capability)]
