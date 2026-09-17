from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException


@dataclass(frozen=True)
class StorageMetadata:
    """Descriptive metadata a storage backend advertises about itself."""

    kind: str
    region: str
    version: str
    tags: tuple = ()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "region": self.region,
            "version": self.version,
            "tags": list(self.tags),
        }


@dataclass
class StorageBackend:
    """A single registered storage backend and its current status."""

    backend_id: str
    capabilities: tuple
    metadata: StorageMetadata
    status: str = "active"
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "backend_id": self.backend_id,
            "capabilities": list(self.capabilities),
            "metadata": self.metadata.to_dict(),
            "status": self.status,
            "registered_at": self.registered_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
        }


_VALID_STATUSES = {"active", "degraded", "offline"}


class StorageRegistry:
    """Tracks storage backends, their capabilities, and their status."""

    def __init__(self) -> None:
        self._backends: dict = {}
        self._capability_index: dict = {}
        self._lock = Lock()

    def register(
        self,
        backend_id: str,
        capabilities: list,
        metadata: StorageMetadata,
        *,
        status: str = "active",
    ) -> StorageBackend:
        if not backend_id:
            raise ValueError("backend_id must be non-empty")
        if status not in _VALID_STATUSES:
            raise ValueError(f"unsupported status '{status}'; expected one of {sorted(_VALID_STATUSES)}")

        backend = StorageBackend(
            backend_id=backend_id,
            capabilities=tuple(capabilities),
            metadata=metadata,
            status=status,
        )
        with self._lock:
            if backend_id in self._backends:
                raise ValueError(f"backend '{backend_id}' is already registered")
            self._backends[backend_id] = backend
            self._index_capabilities(backend)
        return backend

    def remove(self, backend_id: str) -> bool:
        with self._lock:
            backend = self._backends.pop(backend_id, None)
            if backend is None:
                return False
            self._unindex_capabilities(backend)
        return True

    def get(self, backend_id: str) -> Optional[StorageBackend]:
        with self._lock:
            return self._backends.get(backend_id)

    def list_backends(self, *, status: Optional[str] = None, capability: Optional[str] = None) -> list:
        with self._lock:
            if capability is not None:
                backend_ids = self._capability_index.get(capability, set())
                backends = [self._backends[bid] for bid in backend_ids if bid in self._backends]
            else:
                backends = list(self._backends.values())
        if status is not None:
            backends = [backend for backend in backends if backend.status == status]
        return sorted(backends, key=lambda backend: backend.backend_id)

    def is_active(self, backend_id: str) -> bool:
        with self._lock:
            backend = self._backends.get(backend_id)
            return backend is not None and backend.status == "active"

    def set_status(self, backend_id: str, status: str) -> StorageBackend:
        if status not in _VALID_STATUSES:
            raise ValueError(f"unsupported status '{status}'; expected one of {sorted(_VALID_STATUSES)}")
        with self._lock:
            backend = self._backends.get(backend_id)
            if backend is None:
                raise KeyError(backend_id)
            backend.status = status
            backend.last_seen_at = datetime.now(timezone.utc)
            return backend

    def _index_capabilities(self, backend: StorageBackend) -> None:
        for capability in backend.capabilities:
            self._capability_index.setdefault(capability, set()).add(backend.backend_id)

    def _unindex_capabilities(self, backend: StorageBackend) -> None:
        for capability in backend.capabilities:
            bucket = self._capability_index.get(capability)
            if bucket is None:
                continue
            bucket.discard(backend.backend_id)
            if not bucket:
                del self._capability_index[capability]


_storage_registry = StorageRegistry()


def get_storage_registry() -> StorageRegistry:
    return _storage_registry


router = APIRouter(prefix="/storage/backends", tags=["storage-registry"])


@router.post("")
def register_backend_endpoint(
    payload: dict,
    registry: StorageRegistry = Depends(get_storage_registry),
) -> dict:
    try:
        metadata = StorageMetadata(
            kind=payload["metadata"]["kind"],
            region=payload["metadata"]["region"],
            version=payload["metadata"]["version"],
            tags=tuple(payload["metadata"].get("tags", [])),
        )
        backend = registry.register(
            backend_id=payload["backend_id"],
            capabilities=payload.get("capabilities", []),
            metadata=metadata,
            status=payload.get("status", "active"),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return backend.to_dict()


@router.get("")
def list_backends_endpoint(
    status: Optional[str] = None,
    capability: Optional[str] = None,
    registry: StorageRegistry = Depends(get_storage_registry),
) -> list:
    return [backend.to_dict() for backend in registry.list_backends(status=status, capability=capability)]


@router.get("/{backend_id}")
def get_backend_endpoint(
    backend_id: str,
    registry: StorageRegistry = Depends(get_storage_registry),
) -> dict:
    backend = registry.get(backend_id)
    if backend is None:
        raise HTTPException(status_code=404, detail=f"backend '{backend_id}' not found")
    return backend.to_dict()


@router.delete("/{backend_id}")
def remove_backend_endpoint(
    backend_id: str,
    registry: StorageRegistry = Depends(get_storage_registry),
) -> dict:
    removed = registry.remove(backend_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"backend '{backend_id}' not found")
    return {"backend_id": backend_id, "removed": True}
