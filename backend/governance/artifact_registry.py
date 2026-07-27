from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, HTTPException

_CHECKSUM_ALGORITHMS = {
    "md5": 32,
    "sha1": 40,
    "sha256": 64,
}
_HEX_PATTERN = re.compile(r"^[0-9a-fA-F]+$")


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownArtifactError(KeyError):
    pass


class ArtifactAlreadyExistsError(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactMetadata:
    """Validated, immutable metadata describing an artifact's contents."""

    content_type: str
    size_bytes: int
    checksum: str
    checksum_algorithm: str = "sha256"

    def to_dict(self) -> dict:
        return {
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "checksum_algorithm": self.checksum_algorithm,
        }


@dataclass(frozen=True)
class Artifact:
    """An immutable, indexed record of a published deployment artifact."""

    artifact_id: str
    name: str
    version: str
    location: str
    metadata: ArtifactMetadata
    tags: tuple = ()
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "version": self.version,
            "location": self.location,
            "metadata": self.metadata.to_dict(),
            "tags": list(self.tags),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ArtifactRegistry:
    """Central registry for indexing, validating, and discovering artifacts."""

    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}
        self._by_name_version: dict[tuple, str] = {}
        self._lock = Lock()

    def validate_metadata(self, metadata: ArtifactMetadata) -> None:
        if not metadata.content_type:
            raise ValueError("metadata.content_type is required")
        if metadata.size_bytes < 0:
            raise ValueError("metadata.size_bytes must be non-negative")

        algorithm = metadata.checksum_algorithm.lower()
        expected_length = _CHECKSUM_ALGORITHMS.get(algorithm)
        if expected_length is None:
            raise ValueError(
                f"unsupported checksum_algorithm '{metadata.checksum_algorithm}'"
            )
        if not _HEX_PATTERN.match(metadata.checksum or ""):
            raise ValueError("metadata.checksum must be a hexadecimal string")
        if len(metadata.checksum) != expected_length:
            raise ValueError(
                f"checksum length for '{algorithm}' must be {expected_length} characters"
            )

    def publish(
        self,
        name: str,
        version: str,
        *,
        location: str,
        metadata: ArtifactMetadata,
        tags: Iterable[str] = (),
        timestamp: Optional[datetime] = None,
    ) -> Artifact:
        if not name:
            raise ValueError("artifact name is required")
        if not version:
            raise ValueError("artifact version is required")
        if not location:
            raise ValueError("artifact location is required")

        self.validate_metadata(metadata)

        artifact = Artifact(
            artifact_id=_new_id(),
            name=name,
            version=version,
            location=location,
            metadata=metadata,
            tags=tuple(tags),
            created_at=timestamp or datetime.now(timezone.utc),
        )

        key = (name, version)
        with self._lock:
            if key in self._by_name_version:
                raise ArtifactAlreadyExistsError(
                    f"artifact '{name}' version '{version}' is already published"
                )
            self._artifacts[artifact.artifact_id] = artifact
            self._by_name_version[key] = artifact.artifact_id
        return artifact

    def remove(self, artifact_id: str) -> None:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                raise UnknownArtifactError(artifact_id)
            del self._artifacts[artifact_id]
            del self._by_name_version[(artifact.name, artifact.version)]

    def get(self, artifact_id: str) -> Artifact:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            raise UnknownArtifactError(artifact_id)
        return artifact

    def find(self, name: str, version: str) -> Artifact:
        with self._lock:
            artifact_id = self._by_name_version.get((name, version))
        if artifact_id is None:
            raise UnknownArtifactError(f"{name}@{version}")
        return self.get(artifact_id)

    def search(
        self,
        *,
        name: Optional[str] = None,
        version: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> list[Artifact]:
        with self._lock:
            artifacts = list(self._artifacts.values())

        if name is not None:
            artifacts = [artifact for artifact in artifacts if artifact.name == name]
        if version is not None:
            artifacts = [artifact for artifact in artifacts if artifact.version == version]
        if tag is not None:
            artifacts = [artifact for artifact in artifacts if tag in artifact.tags]
        return artifacts


_registry = ArtifactRegistry()


def get_artifact_registry() -> ArtifactRegistry:
    return _registry


router = APIRouter(prefix="/governance", tags=["governance-artifacts"])


@router.post("/artifacts")
def publish_artifact(payload: dict = Body(...)) -> dict:
    name = payload.get("name")
    version = payload.get("version")
    location = payload.get("location")
    metadata_payload = payload.get("metadata") or {}

    if not name or not version or not location:
        raise HTTPException(status_code=422, detail="name, version and location are required")

    try:
        metadata = ArtifactMetadata(
            content_type=metadata_payload.get("content_type", ""),
            size_bytes=metadata_payload.get("size_bytes", -1),
            checksum=metadata_payload.get("checksum", ""),
            checksum_algorithm=metadata_payload.get("checksum_algorithm", "sha256"),
        )
        artifact = get_artifact_registry().publish(
            name,
            version,
            location=location,
            metadata=metadata,
            tags=payload.get("tags", ()),
        )
    except ArtifactAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return artifact.to_dict()


@router.get("/artifacts")
def search_artifacts(name: Optional[str] = None, version: Optional[str] = None, tag: Optional[str] = None) -> list[dict]:
    results = get_artifact_registry().search(name=name, version=version, tag=tag)
    return [artifact.to_dict() for artifact in results]


@router.get("/artifacts/{artifact}")
def get_artifact(artifact: str) -> dict:
    try:
        result = get_artifact_registry().get(artifact)
    except UnknownArtifactError:
        raise HTTPException(status_code=404, detail="unknown artifact")
    return result.to_dict()
