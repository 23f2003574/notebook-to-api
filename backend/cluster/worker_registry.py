from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException


@dataclass(frozen=True)
class WorkerMetadata:
    """Discovery metadata a worker advertises about itself."""

    hostname: str
    region: str
    version: str
    tags: tuple = ()

    def to_dict(self) -> dict:
        return {
            "hostname": self.hostname,
            "region": self.region,
            "version": self.version,
            "tags": list(self.tags),
        }


@dataclass
class WorkerNode:
    """A single registered worker and its current status."""

    worker_id: str
    capabilities: tuple
    metadata: WorkerMetadata
    status: str = "online"
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "capabilities": list(self.capabilities),
            "metadata": self.metadata.to_dict(),
            "status": self.status,
            "registered_at": self.registered_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
        }


_VALID_STATUSES = {"online", "draining", "offline"}


class WorkerRegistry:
    """Tracks cluster workers, their capabilities, and their status."""

    def __init__(self) -> None:
        self._workers: dict = {}
        self._capability_index: dict = {}
        self._lock = Lock()

    def register(
        self,
        worker_id: str,
        capabilities: list,
        metadata: WorkerMetadata,
        *,
        status: str = "online",
    ) -> WorkerNode:
        if not worker_id:
            raise ValueError("worker_id must be non-empty")
        if status not in _VALID_STATUSES:
            raise ValueError(f"unsupported status '{status}'; expected one of {sorted(_VALID_STATUSES)}")

        node = WorkerNode(
            worker_id=worker_id,
            capabilities=tuple(capabilities),
            metadata=metadata,
            status=status,
        )
        with self._lock:
            existing = self._workers.get(worker_id)
            if existing is not None:
                self._unindex_capabilities(existing)
            self._workers[worker_id] = node
            self._index_capabilities(node)
        return node

    def unregister(self, worker_id: str) -> bool:
        with self._lock:
            node = self._workers.pop(worker_id, None)
            if node is None:
                return False
            self._unindex_capabilities(node)
        return True

    def get(self, worker_id: str) -> Optional[WorkerNode]:
        with self._lock:
            return self._workers.get(worker_id)

    def list_workers(self, *, status: Optional[str] = None, capability: Optional[str] = None) -> list:
        with self._lock:
            if capability is not None:
                worker_ids = self._capability_index.get(capability, set())
                workers = [self._workers[wid] for wid in worker_ids if wid in self._workers]
            else:
                workers = list(self._workers.values())
        if status is not None:
            workers = [worker for worker in workers if worker.status == status]
        return sorted(workers, key=lambda worker: worker.worker_id)

    def set_status(self, worker_id: str, status: str) -> WorkerNode:
        if status not in _VALID_STATUSES:
            raise ValueError(f"unsupported status '{status}'; expected one of {sorted(_VALID_STATUSES)}")
        with self._lock:
            node = self._workers.get(worker_id)
            if node is None:
                raise KeyError(worker_id)
            node.status = status
            node.last_seen_at = datetime.now(timezone.utc)
            return node

    def _index_capabilities(self, node: WorkerNode) -> None:
        for capability in node.capabilities:
            self._capability_index.setdefault(capability, set()).add(node.worker_id)

    def _unindex_capabilities(self, node: WorkerNode) -> None:
        for capability in node.capabilities:
            bucket = self._capability_index.get(capability)
            if bucket is None:
                continue
            bucket.discard(node.worker_id)
            if not bucket:
                del self._capability_index[capability]


_worker_registry = WorkerRegistry()


def get_worker_registry() -> WorkerRegistry:
    return _worker_registry


router = APIRouter(prefix="/cluster/workers", tags=["worker-registry"])


@router.post("")
def register_worker_endpoint(
    payload: dict,
    registry: WorkerRegistry = Depends(get_worker_registry),
) -> dict:
    try:
        metadata = WorkerMetadata(
            hostname=payload["metadata"]["hostname"],
            region=payload["metadata"]["region"],
            version=payload["metadata"]["version"],
            tags=tuple(payload["metadata"].get("tags", [])),
        )
        node = registry.register(
            worker_id=payload["worker_id"],
            capabilities=payload.get("capabilities", []),
            metadata=metadata,
            status=payload.get("status", "online"),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return node.to_dict()


@router.get("")
def list_workers_endpoint(
    status: Optional[str] = None,
    capability: Optional[str] = None,
    registry: WorkerRegistry = Depends(get_worker_registry),
) -> list:
    return [worker.to_dict() for worker in registry.list_workers(status=status, capability=capability)]


@router.get("/{worker_id}")
def get_worker_endpoint(
    worker_id: str,
    registry: WorkerRegistry = Depends(get_worker_registry),
) -> dict:
    node = registry.get(worker_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"worker '{worker_id}' not found")
    return node.to_dict()


@router.delete("/{worker_id}")
def unregister_worker_endpoint(
    worker_id: str,
    registry: WorkerRegistry = Depends(get_worker_registry),
) -> dict:
    removed = registry.unregister(worker_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"worker '{worker_id}' not found")
    return {"worker_id": worker_id, "removed": True}
