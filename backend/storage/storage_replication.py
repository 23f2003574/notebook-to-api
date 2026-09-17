from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .object_storage import ObjectStorageEngine, get_object_storage_engine


class ReplicationMode(str, Enum):
    """The strategies replicate()/sync() can run under."""

    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"
    INCREMENTAL = "incremental"
    FULL = "full"


class JobStatus(str, Enum):
    """The outcome of a replication job."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class ReplicationJob:
    """A record of one replicate()/sync() execution against a set of replicas."""

    job_id: str
    key: str
    mode: ReplicationMode
    replica_ids: tuple
    status: JobStatus
    results: dict
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "key": self.key,
            "mode": self.mode.value,
            "replica_ids": list(self.replica_ids),
            "status": self.status.value,
            "results": dict(self.results),
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }


@dataclass
class ReplicationStatus:
    """The current sync state of a single key on a single replica."""

    key: str
    replica_id: str
    in_sync: bool
    checksum: Optional[str]
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "replica_id": self.replica_id,
            "in_sync": self.in_sync,
            "checksum": self.checksum,
            "checked_at": self.checked_at.isoformat(),
        }


class StorageReplicationEngine:
    """Synchronizes objects from a primary backend to registered replica backends."""

    def __init__(self, *, primary: ObjectStorageEngine, replicas: Optional[dict] = None) -> None:
        self._primary = primary
        self._replicas: dict = dict(replicas or {})
        self._jobs: dict = {}
        self._lock = Lock()

    def add_replica(self, replica_id: str, engine: ObjectStorageEngine) -> None:
        if not replica_id:
            raise ValueError("replica_id must be non-empty")
        with self._lock:
            self._replicas[replica_id] = engine

    def replicate(
        self,
        key: str,
        replica_ids: Optional[list] = None,
        *,
        mode: ReplicationMode = ReplicationMode.FULL,
    ) -> ReplicationJob:
        primary_obj = self._primary.get(key)
        if primary_obj is None:
            raise KeyError(key)

        with self._lock:
            target_ids = list(replica_ids) if replica_ids is not None else list(self._replicas.keys())
            replicas = dict(self._replicas)

        if not target_ids:
            raise ValueError("no replica targets configured")

        results = {}
        for replica_id in target_ids:
            replica_engine = replicas.get(replica_id)
            if replica_engine is None:
                results[replica_id] = False
                continue

            if mode == ReplicationMode.INCREMENTAL:
                existing = replica_engine.get(key)
                if existing is not None and existing.metadata.checksum == primary_obj.metadata.checksum:
                    results[replica_id] = True
                    continue

            replica_engine.put(key, primary_obj.data, content_type=primary_obj.metadata.content_type)
            results[replica_id] = True

        if all(results.values()):
            status = JobStatus.COMPLETED
        elif any(results.values()):
            status = JobStatus.PARTIAL
        else:
            status = JobStatus.FAILED

        job = ReplicationJob(
            job_id=uuid.uuid4().hex,
            key=key,
            mode=mode,
            replica_ids=tuple(target_ids),
            status=status,
            results=results,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def sync(self, key: Optional[str] = None, *, mode: ReplicationMode = ReplicationMode.INCREMENTAL) -> list:
        keys = [key] if key is not None else self._primary.list_keys()
        jobs = []
        for k in keys:
            try:
                jobs.append(self.replicate(k, mode=mode))
            except (KeyError, ValueError):
                continue
        return jobs

    def verify(self, key: str, replica_id: str) -> ReplicationStatus:
        primary_obj = self._primary.get(key)
        if primary_obj is None:
            raise KeyError(key)

        with self._lock:
            replica_engine = self._replicas.get(replica_id)
        if replica_engine is None:
            raise KeyError(replica_id)

        replica_obj = replica_engine.get(key)
        if replica_obj is None:
            return ReplicationStatus(key=key, replica_id=replica_id, in_sync=False, checksum=None)

        in_sync = replica_obj.metadata.checksum == primary_obj.metadata.checksum
        return ReplicationStatus(key=key, replica_id=replica_id, in_sync=in_sync, checksum=replica_obj.metadata.checksum)

    def repair(self, key: str, *, replica_id: Optional[str] = None) -> list:
        with self._lock:
            candidate_ids = [replica_id] if replica_id is not None else list(self._replicas.keys())

        jobs = []
        for rid in candidate_ids:
            status = self.verify(key, rid)
            if not status.in_sync:
                jobs.append(self.replicate(key, [rid], mode=ReplicationMode.FULL))
        return jobs

    def list_status(self) -> list:
        with self._lock:
            replica_ids = list(self._replicas.keys())
        statuses = []
        for key in self._primary.list_keys():
            for replica_id in replica_ids:
                statuses.append(self.verify(key, replica_id))
        return statuses

    def get_job(self, job_id: str) -> Optional[ReplicationJob]:
        with self._lock:
            return self._jobs.get(job_id)


_storage_replication_engine = StorageReplicationEngine(primary=get_object_storage_engine())


def get_storage_replication_engine() -> StorageReplicationEngine:
    return _storage_replication_engine


router = APIRouter(prefix="/storage/replication", tags=["storage-replication"])


@router.post("")
def replicate_endpoint(
    key: str,
    replica_ids: Optional[str] = None,
    mode: ReplicationMode = ReplicationMode.FULL,
    engine: StorageReplicationEngine = Depends(get_storage_replication_engine),
) -> dict:
    targets = replica_ids.split(",") if replica_ids else None
    try:
        job = engine.replicate(key, targets, mode=mode)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"object '{key}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return job.to_dict()


@router.post("/sync")
def sync_endpoint(
    key: Optional[str] = None,
    mode: ReplicationMode = ReplicationMode.INCREMENTAL,
    engine: StorageReplicationEngine = Depends(get_storage_replication_engine),
) -> list:
    return [job.to_dict() for job in engine.sync(key, mode=mode)]


@router.get("/status")
def status_endpoint(
    engine: StorageReplicationEngine = Depends(get_storage_replication_engine),
) -> list:
    return [status.to_dict() for status in engine.list_status()]


@router.get("/{job_id}")
def get_job_endpoint(
    job_id: str,
    engine: StorageReplicationEngine = Depends(get_storage_replication_engine),
) -> dict:
    job = engine.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    return job.to_dict()
