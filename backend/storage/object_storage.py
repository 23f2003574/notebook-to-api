from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Iterable, Iterator, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .storage_registry import StorageRegistry, get_storage_registry

_STREAM_CHUNK_SIZE = 64 * 1024


class ObjectStorageBackend(str, Enum):
    """Backends the object storage engine can persist objects to."""

    LOCAL = "local"
    S3 = "s3"
    AZURE_BLOB = "azure_blob"
    GCS = "gcs"


@dataclass(frozen=True)
class ObjectMetadata:
    """Descriptive metadata attached to a stored object."""

    content_type: str
    size: int
    checksum: str
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        return {
            "content_type": self.content_type,
            "size": self.size,
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class StorageObject:
    """A stored object and the data backing it."""

    key: str
    backend: ObjectStorageBackend
    data: bytes
    metadata: ObjectMetadata

    def to_dict(self, *, include_data: bool = False) -> dict:
        payload = {
            "key": self.key,
            "backend": self.backend.value,
            "metadata": self.metadata.to_dict(),
        }
        if include_data:
            payload["data"] = self.data
        return payload


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObjectStorageEngine:
    """Pluggable engine for storing and retrieving artifacts across backends."""

    def __init__(self, *, registry: Optional[StorageRegistry] = None) -> None:
        self._objects: dict = {}
        self._lock = Lock()
        self._registry = registry

    def put(
        self,
        key: str,
        data: Union[bytes, Iterable[bytes]],
        *,
        backend: ObjectStorageBackend = ObjectStorageBackend.LOCAL,
        content_type: str = "application/octet-stream",
        backend_id: Optional[str] = None,
        expected_checksum: Optional[str] = None,
    ) -> StorageObject:
        if not key:
            raise ValueError("key must be non-empty")
        self._require_active_backend(backend_id)

        payload = data if isinstance(data, (bytes, bytearray)) else b"".join(data)
        payload = bytes(payload)
        if expected_checksum is not None and _checksum(payload) != expected_checksum:
            raise ValueError(f"checksum mismatch for key '{key}'")
        now = datetime.now(timezone.utc)

        with self._lock:
            existing = self._objects.get(key)
            created_at = existing.metadata.created_at if existing is not None else now
            obj = StorageObject(
                key=key,
                backend=backend,
                data=payload,
                metadata=ObjectMetadata(
                    content_type=content_type,
                    size=len(payload),
                    checksum=_checksum(payload),
                    created_at=created_at,
                    updated_at=now,
                ),
            )
            self._objects[key] = obj
        return obj

    def get(self, key: str) -> Optional[StorageObject]:
        with self._lock:
            return self._objects.get(key)

    def get_stream(self, key: str, *, chunk_size: int = _STREAM_CHUNK_SIZE) -> Iterator[bytes]:
        obj = self.get(key)
        if obj is None:
            raise KeyError(key)
        data = obj.data
        for offset in range(0, len(data), chunk_size):
            yield data[offset : offset + chunk_size]

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._objects.pop(key, None) is not None

    def move(self, old_key: str, new_key: str) -> StorageObject:
        if not new_key:
            raise ValueError("new_key must be non-empty")
        with self._lock:
            if old_key not in self._objects:
                raise KeyError(old_key)
            if new_key in self._objects:
                raise ValueError(f"key '{new_key}' already exists")
            obj = self._objects.pop(old_key)
            moved = StorageObject(
                key=new_key,
                backend=obj.backend,
                data=obj.data,
                metadata=obj.metadata,
            )
            self._objects[new_key] = moved
        return moved

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._objects

    def _require_active_backend(self, backend_id: Optional[str]) -> None:
        if backend_id is None or self._registry is None:
            return
        if not self._registry.is_active(backend_id):
            raise ValueError(f"backend '{backend_id}' is not registered or not active")


_object_storage_engine = ObjectStorageEngine(registry=get_storage_registry())


def get_object_storage_engine() -> ObjectStorageEngine:
    return _object_storage_engine


router = APIRouter(prefix="/storage/objects", tags=["object-storage"])


@router.put("/{key:path}")
async def put_object_endpoint(
    key: str,
    request: Request,
    content_type: str = "application/octet-stream",
    backend: ObjectStorageBackend = ObjectStorageBackend.LOCAL,
    backend_id: Optional[str] = None,
    engine: ObjectStorageEngine = Depends(get_object_storage_engine),
) -> dict:
    data = await request.body()
    try:
        obj = engine.put(key, data, backend=backend, content_type=content_type, backend_id=backend_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return obj.to_dict()


@router.get("/{key:path}")
def get_object_endpoint(
    key: str,
    engine: ObjectStorageEngine = Depends(get_object_storage_engine),
) -> Response:
    obj = engine.get(key)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"object '{key}' not found")
    return Response(content=obj.data, media_type=obj.metadata.content_type)


@router.head("/{key:path}")
def head_object_endpoint(
    key: str,
    engine: ObjectStorageEngine = Depends(get_object_storage_engine),
) -> Response:
    obj = engine.get(key)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"object '{key}' not found")
    headers = {
        "Content-Length": str(obj.metadata.size),
        "Content-Type": obj.metadata.content_type,
        "ETag": obj.metadata.checksum,
    }
    return Response(headers=headers, media_type=obj.metadata.content_type)


@router.delete("/{key:path}")
def delete_object_endpoint(
    key: str,
    engine: ObjectStorageEngine = Depends(get_object_storage_engine),
) -> dict:
    removed = engine.delete(key)
    if not removed:
        raise HTTPException(status_code=404, detail=f"object '{key}' not found")
    return {"key": key, "removed": True}
