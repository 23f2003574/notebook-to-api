from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Optional, Union

from fastapi import APIRouter, Body, HTTPException

from .artifact_registry import ArtifactRegistry, UnknownArtifactError, get_artifact_registry

REPLICATION_STATUSES = ("SUCCEEDED", "FAILED")


def _new_id() -> str:
    return uuid.uuid4().hex


def _default_transport(artifact, target: "ReplicationTarget") -> str:
    return artifact.metadata.checksum


class UnknownReplicaError(KeyError):
    pass


@dataclass(frozen=True)
class ReplicationTarget:
    """A destination registry or storage backend an artifact can be copied to."""

    name: str
    endpoint: str

    def to_dict(self) -> dict:
        return {"name": self.name, "endpoint": self.endpoint}


@dataclass(frozen=True)
class ReplicationRecord:
    """An immutable snapshot of one artifact's replication state to one target."""

    record_id: str
    artifact_id: str
    target: ReplicationTarget
    status: str
    attempts: int = 0
    checksum_verified: bool = False
    last_error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "artifact_id": self.artifact_id,
            "target": self.target.to_dict(),
            "status": self.status,
            "attempts": self.attempts,
            "checksum_verified": self.checksum_verified,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ArtifactReplicationService:
    """Copies published artifacts to remote registries and tracks replica health."""

    def __init__(
        self,
        registry: Optional[ArtifactRegistry] = None,
        transport: Optional[Callable[[object, ReplicationTarget], str]] = None,
    ) -> None:
        self._registry = registry or get_artifact_registry()
        self._transport = transport or _default_transport
        self._records: dict[tuple, ReplicationRecord] = {}
        self._lock = Lock()

    def _attempt_transfer(
        self, artifact, target: ReplicationTarget, *, max_attempts: int
    ) -> tuple:
        attempts = 0
        status = "FAILED"
        checksum_verified = False
        last_error = None

        while attempts < max_attempts:
            attempts += 1
            try:
                replica_checksum = self._transport(artifact, target)
            except Exception as exc:
                last_error = str(exc)
                continue

            if self._registry.verify_checksum(artifact.artifact_id, replica_checksum):
                status = "SUCCEEDED"
                checksum_verified = True
                last_error = None
                break
            last_error = "checksum mismatch"

        return attempts, status, checksum_verified, last_error

    def replicate(
        self,
        artifact_id: str,
        target_name: str,
        endpoint: str,
        *,
        max_attempts: int = 3,
        timestamp: Optional[datetime] = None,
    ) -> ReplicationRecord:
        artifact = self._registry.get(artifact_id)
        if not target_name:
            raise ValueError("target name is required")
        if not endpoint:
            raise ValueError("target endpoint is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        target = ReplicationTarget(name=target_name, endpoint=endpoint)
        attempts, status, checksum_verified, last_error = self._attempt_transfer(
            artifact, target, max_attempts=max_attempts
        )

        now = timestamp or datetime.now(timezone.utc)
        record = ReplicationRecord(
            record_id=_new_id(),
            artifact_id=artifact_id,
            target=target,
            status=status,
            attempts=attempts,
            checksum_verified=checksum_verified,
            last_error=last_error,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._records[(artifact_id, target_name)] = record
        return record

    def sync(
        self,
        artifact_id: str,
        target_name: str,
        *,
        max_attempts: int = 3,
        timestamp: Optional[datetime] = None,
    ) -> ReplicationRecord:
        key = (artifact_id, target_name)
        with self._lock:
            existing = self._records.get(key)
        if existing is None:
            raise UnknownReplicaError(f"{artifact_id}@{target_name}")

        artifact = self._registry.get(artifact_id)
        attempts, status, checksum_verified, last_error = self._attempt_transfer(
            artifact, existing.target, max_attempts=max_attempts
        )

        updated = replace(
            existing,
            status=status,
            attempts=existing.attempts + attempts,
            checksum_verified=checksum_verified,
            last_error=last_error,
            updated_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._records[key] = updated
        return updated

    def status(
        self, artifact_id: str, target_name: Optional[str] = None
    ) -> Union[ReplicationRecord, list]:
        if target_name is not None:
            with self._lock:
                record = self._records.get((artifact_id, target_name))
            if record is None:
                raise UnknownReplicaError(f"{artifact_id}@{target_name}")
            return record

        with self._lock:
            return [
                record
                for (record_artifact_id, _), record in self._records.items()
                if record_artifact_id == artifact_id
            ]

    def remove_replica(self, artifact_id: str, target_name: str) -> None:
        key = (artifact_id, target_name)
        with self._lock:
            if key not in self._records:
                raise UnknownReplicaError(f"{artifact_id}@{target_name}")
            del self._records[key]


_replication_service = ArtifactReplicationService()


def get_artifact_replication_service() -> ArtifactReplicationService:
    return _replication_service


router = APIRouter(prefix="/governance", tags=["governance-artifact-replication"])


@router.post("/artifacts/{artifact}/replicate")
def replicate_artifact(artifact: str, payload: dict = Body(...)) -> dict:
    target_name = payload.get("target_name")
    endpoint = payload.get("endpoint")
    if not target_name or not endpoint:
        raise HTTPException(status_code=422, detail="target_name and endpoint are required")

    try:
        record = get_artifact_replication_service().replicate(
            artifact,
            target_name,
            endpoint,
            max_attempts=payload.get("max_attempts", 3),
        )
    except UnknownArtifactError:
        raise HTTPException(status_code=404, detail="unknown artifact")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return record.to_dict()


@router.get("/artifacts/{artifact}/replication")
def get_replication_status(artifact: str) -> list:
    records = get_artifact_replication_service().status(artifact)
    return [record.to_dict() for record in records]


@router.delete("/artifacts/{artifact}/replicas/{target}")
def delete_replica(artifact: str, target: str) -> dict:
    try:
        get_artifact_replication_service().remove_replica(artifact, target)
    except UnknownReplicaError:
        raise HTTPException(status_code=404, detail="unknown replica")
    return {"artifact_id": artifact, "target": target, "removed": True}
