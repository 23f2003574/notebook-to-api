from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from .artifact_manager import ArtifactManager, ArtifactManifest, get_artifact_manager
from .object_storage import ObjectStorageEngine, get_object_storage_engine


@dataclass(frozen=True)
class VersionSnapshot:
    """The immutable content signature captured at version creation time."""

    content_type: str
    size: int
    checksum: str

    def to_dict(self) -> dict:
        return {
            "content_type": self.content_type,
            "size": self.size,
            "checksum": self.checksum,
        }


@dataclass
class StorageVersion:
    """A single, immutable point in an artifact's version history."""

    version_id: str
    artifact_id: str
    version_number: int
    object_key: str
    snapshot: VersionSnapshot
    comment: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "version_id": self.version_id,
            "artifact_id": self.artifact_id,
            "version_number": self.version_number,
            "object_key": self.object_key,
            "snapshot": self.snapshot.to_dict(),
            "comment": self.comment,
            "created_at": self.created_at.isoformat(),
        }


def _version_key(artifact_id: str, version_number: int) -> str:
    return f"versions/{artifact_id}/v{version_number}"


class StorageVersionManager:
    """Tracks immutable version history for artifacts and supports rollback."""

    def __init__(
        self,
        *,
        object_storage: ObjectStorageEngine,
        artifact_manager: Optional[ArtifactManager] = None,
    ) -> None:
        self._object_storage = object_storage
        self._artifact_manager = artifact_manager
        self._versions: dict = {}
        self._lock = Lock()

    def create_version(
        self,
        artifact_id: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        comment: Optional[str] = None,
    ) -> StorageVersion:
        if not artifact_id:
            raise ValueError("artifact_id must be non-empty")

        with self._lock:
            history = self._versions.setdefault(artifact_id, [])
            version_number = len(history) + 1
            object_key = _version_key(artifact_id, version_number)
            stored = self._object_storage.put(object_key, data, content_type=content_type)
            version = StorageVersion(
                version_id=uuid.uuid4().hex,
                artifact_id=artifact_id,
                version_number=version_number,
                object_key=stored.key,
                snapshot=VersionSnapshot(
                    content_type=stored.metadata.content_type,
                    size=stored.metadata.size,
                    checksum=stored.metadata.checksum,
                ),
                comment=comment,
            )
            history.append(version)

        self._sync_artifact(version)
        return version

    def latest(self, artifact_id: str) -> StorageVersion:
        with self._lock:
            history = self._versions.get(artifact_id, [])
            if not history:
                raise KeyError(artifact_id)
            return history[-1]

    def history(self, artifact_id: str) -> list:
        with self._lock:
            return list(self._versions.get(artifact_id, []))

    def rollback(self, artifact_id: str, version_number: int) -> StorageVersion:
        with self._lock:
            history = self._versions.get(artifact_id, [])
            target = next((v for v in history if v.version_number == version_number), None)
        if target is None:
            raise KeyError(f"version {version_number} not found for artifact '{artifact_id}'")

        target_object = self._object_storage.get(target.object_key)
        if target_object is None:
            raise KeyError(target.object_key)

        return self.create_version(
            artifact_id,
            target_object.data,
            content_type=target.snapshot.content_type,
            comment=f"rollback to v{version_number}",
        )

    def _sync_artifact(self, version: StorageVersion) -> None:
        if self._artifact_manager is None:
            return
        try:
            self._artifact_manager.update_manifest(
                version.artifact_id,
                version.object_key,
                ArtifactManifest(
                    content_type=version.snapshot.content_type,
                    size=version.snapshot.size,
                    checksum=version.snapshot.checksum,
                ),
            )
        except KeyError:
            pass


_storage_version_manager = StorageVersionManager(
    object_storage=get_object_storage_engine(),
    artifact_manager=get_artifact_manager(),
)


def get_storage_version_manager() -> StorageVersionManager:
    return _storage_version_manager


router = APIRouter(prefix="/storage/versions", tags=["storage-versioning"])


@router.post("")
async def create_version_endpoint(
    request: Request,
    artifact_id: str,
    content_type: str = "application/octet-stream",
    comment: Optional[str] = None,
    manager: StorageVersionManager = Depends(get_storage_version_manager),
) -> dict:
    data = await request.body()
    try:
        version = manager.create_version(artifact_id, data, content_type=content_type, comment=comment)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return version.to_dict()


@router.get("/{artifact_id}")
def history_endpoint(
    artifact_id: str,
    manager: StorageVersionManager = Depends(get_storage_version_manager),
) -> list:
    return [version.to_dict() for version in manager.history(artifact_id)]


@router.get("/{artifact_id}/latest")
def latest_endpoint(
    artifact_id: str,
    manager: StorageVersionManager = Depends(get_storage_version_manager),
) -> dict:
    try:
        version = manager.latest(artifact_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no versions found for artifact '{artifact_id}'")
    return version.to_dict()


@router.post("/{artifact_id}/rollback")
def rollback_endpoint(
    artifact_id: str,
    version_number: int,
    manager: StorageVersionManager = Depends(get_storage_version_manager),
) -> dict:
    try:
        version = manager.rollback(artifact_id, version_number)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"version {version_number} not found for artifact '{artifact_id}'",
        )
    return version.to_dict()
