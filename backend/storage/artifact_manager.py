from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from .object_storage import ObjectStorageEngine, get_object_storage_engine


class ArtifactType(str, Enum):
    """The kinds of artifacts the manager can catalog."""

    NOTEBOOK = "notebook"
    COMPILED_API = "compiled_api"
    SDK = "sdk"
    MODEL = "model"
    DATASET = "dataset"
    LOG_BUNDLE = "log_bundle"


@dataclass(frozen=True)
class ArtifactManifest:
    """Indexed metadata describing an artifact's stored payload."""

    content_type: str
    size: int
    checksum: str
    tags: tuple = ()

    def to_dict(self) -> dict:
        return {
            "content_type": self.content_type,
            "size": self.size,
            "checksum": self.checksum,
            "tags": list(self.tags),
        }


@dataclass
class Artifact:
    """A cataloged artifact and where its payload lives in object storage."""

    artifact_id: str
    name: str
    artifact_type: ArtifactType
    namespace: str
    object_key: str
    manifest: ArtifactManifest
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "artifact_type": self.artifact_type.value,
            "namespace": self.namespace,
            "object_key": self.object_key,
            "manifest": self.manifest.to_dict(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def _object_key(namespace: str, artifact_type: ArtifactType, artifact_id: str, name: str) -> str:
    return f"{namespace}/{artifact_type.value}/{artifact_id}/{name}"


class ArtifactManager:
    """Catalogs artifacts and tracks their lifecycle on top of object storage."""

    def __init__(self, *, object_storage: ObjectStorageEngine) -> None:
        self._object_storage = object_storage
        self._artifacts: dict = {}
        self._namespace_index: dict = {}
        self._lock = Lock()

    def create(
        self,
        name: str,
        artifact_type: ArtifactType,
        data: bytes,
        *,
        namespace: str = "default",
        content_type: str = "application/octet-stream",
        tags: Optional[list] = None,
    ) -> Artifact:
        if not name:
            raise ValueError("name must be non-empty")

        artifact_id = uuid.uuid4().hex
        object_key = _object_key(namespace, artifact_type, artifact_id, name)
        stored = self._object_storage.put(object_key, data, content_type=content_type)

        artifact = Artifact(
            artifact_id=artifact_id,
            name=name,
            artifact_type=artifact_type,
            namespace=namespace,
            object_key=stored.key,
            manifest=ArtifactManifest(
                content_type=stored.metadata.content_type,
                size=stored.metadata.size,
                checksum=stored.metadata.checksum,
                tags=tuple(tags or ()),
            ),
        )
        with self._lock:
            self._artifacts[artifact_id] = artifact
            self._namespace_index.setdefault(namespace, set()).add(artifact_id)
        return artifact

    def fetch(self, artifact_id: str) -> Optional[Artifact]:
        with self._lock:
            return self._artifacts.get(artifact_id)

    def list_artifacts(self, *, namespace: Optional[str] = None, artifact_type: Optional[ArtifactType] = None) -> list:
        with self._lock:
            if namespace is not None:
                artifact_ids = self._namespace_index.get(namespace, set())
                artifacts = [self._artifacts[aid] for aid in artifact_ids if aid in self._artifacts]
            else:
                artifacts = list(self._artifacts.values())
        if artifact_type is not None:
            artifacts = [artifact for artifact in artifacts if artifact.artifact_type == artifact_type]
        return sorted(artifacts, key=lambda artifact: artifact.artifact_id)

    def move(self, artifact_id: str, new_namespace: str) -> Artifact:
        if not new_namespace:
            raise ValueError("new_namespace must be non-empty")
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                raise KeyError(artifact_id)
            old_namespace = artifact.namespace
            new_key = _object_key(new_namespace, artifact.artifact_type, artifact.artifact_id, artifact.name)

        moved_object = self._object_storage.move(artifact.object_key, new_key)

        with self._lock:
            artifact.namespace = new_namespace
            artifact.object_key = moved_object.key
            artifact.updated_at = datetime.now(timezone.utc)
            self._namespace_index.get(old_namespace, set()).discard(artifact_id)
            self._namespace_index.setdefault(new_namespace, set()).add(artifact_id)
        return artifact

    def delete(self, artifact_id: str) -> bool:
        with self._lock:
            artifact = self._artifacts.pop(artifact_id, None)
            if artifact is None:
                return False
            self._namespace_index.get(artifact.namespace, set()).discard(artifact_id)
        self._object_storage.delete(artifact.object_key)
        return True


_object_storage_engine = get_object_storage_engine()
_artifact_manager = ArtifactManager(object_storage=_object_storage_engine)


def get_artifact_manager() -> ArtifactManager:
    return _artifact_manager


router = APIRouter(prefix="/storage/artifacts", tags=["artifact-manager"])


@router.post("")
async def create_artifact_endpoint(
    request: Request,
    name: str,
    artifact_type: ArtifactType,
    namespace: str = "default",
    content_type: str = "application/octet-stream",
    tags: Optional[str] = None,
    manager: ArtifactManager = Depends(get_artifact_manager),
) -> dict:
    data = await request.body()
    try:
        artifact = manager.create(
            name,
            artifact_type,
            data,
            namespace=namespace,
            content_type=content_type,
            tags=tags.split(",") if tags else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return artifact.to_dict()


@router.get("")
def list_artifacts_endpoint(
    namespace: Optional[str] = None,
    artifact_type: Optional[ArtifactType] = None,
    manager: ArtifactManager = Depends(get_artifact_manager),
) -> list:
    return [
        artifact.to_dict()
        for artifact in manager.list_artifacts(namespace=namespace, artifact_type=artifact_type)
    ]


@router.get("/{artifact_id}")
def fetch_artifact_endpoint(
    artifact_id: str,
    manager: ArtifactManager = Depends(get_artifact_manager),
) -> dict:
    artifact = manager.fetch(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"artifact '{artifact_id}' not found")
    return artifact.to_dict()


@router.delete("/{artifact_id}")
def delete_artifact_endpoint(
    artifact_id: str,
    manager: ArtifactManager = Depends(get_artifact_manager),
) -> dict:
    removed = manager.delete(artifact_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"artifact '{artifact_id}' not found")
    return {"artifact_id": artifact_id, "removed": True}
