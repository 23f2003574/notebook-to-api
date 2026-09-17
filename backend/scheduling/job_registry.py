from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query


class JobAlreadyRegisteredError(ValueError):
    pass


class UnknownJobError(KeyError):
    pass


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobMetadata:
    """Descriptive information attached to a registered job."""

    description: str = ""
    owner: str = ""
    tags: tuple = ()
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "owner": self.owner,
            "tags": list(self.tags),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "JobMetadata":
        payload = payload or {}
        return cls(
            description=payload.get("description", ""),
            owner=payload.get("owner", ""),
            tags=tuple(payload.get("tags", ())),
            source=payload.get("source", ""),
        )


@dataclass(frozen=True)
class Job:
    """A single registered job and its current status."""

    job_id: str
    job_type: str
    metadata: JobMetadata
    status: JobStatus
    registered_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "metadata": self.metadata.to_dict(),
            "status": self.status.value,
            "registered_at": self.registered_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class JobRegistry:
    """Tracks registered jobs, their status, and discovery metadata."""

    def __init__(self) -> None:
        self._jobs: dict = {}
        self._tag_index: dict = {}
        self._lock = Lock()

    def register(
        self,
        job_id: str,
        job_type: str,
        metadata: Optional[JobMetadata] = None,
        *,
        status: JobStatus = JobStatus.PENDING,
    ) -> Job:
        if not job_id:
            raise ValueError("job id is required")
        if not job_type:
            raise ValueError("job type is required")
        metadata = metadata or JobMetadata()
        with self._lock:
            if job_id in self._jobs:
                raise JobAlreadyRegisteredError(f"{job_id} is already registered")
            now = datetime.now(timezone.utc)
            job = Job(
                job_id=job_id,
                job_type=job_type,
                metadata=metadata,
                status=status,
                registered_at=now,
                updated_at=now,
            )
            self._jobs[job_id] = job
            for tag in metadata.tags:
                self._tag_index.setdefault(tag, set()).add(job_id)
        return job

    def remove(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if job is None:
                raise UnknownJobError(job_id)
            for tag in job.metadata.tags:
                names = self._tag_index.get(tag)
                if names is not None:
                    names.discard(job_id)
                    if not names:
                        del self._tag_index[tag]

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise UnknownJobError(job_id)
            return job

    def is_registered(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._jobs

    def update_status(self, job_id: str, status: JobStatus) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise UnknownJobError(job_id)
            updated = Job(
                job_id=job.job_id,
                job_type=job.job_type,
                metadata=job.metadata,
                status=status,
                registered_at=job.registered_at,
                updated_at=datetime.now(timezone.utc),
            )
            self._jobs[job_id] = updated
            return updated

    def list_jobs(self, tag: Optional[str] = None, status: Optional[JobStatus] = None) -> list:
        with self._lock:
            if tag is not None:
                job_ids = sorted(self._tag_index.get(tag, set()))
            else:
                job_ids = sorted(self._jobs)
            jobs = [self._jobs[job_id] for job_id in job_ids if job_id in self._jobs]
            if status is not None:
                jobs = [job for job in jobs if job.status == status]
            return jobs


_job_registry = JobRegistry()


def get_job_registry() -> JobRegistry:
    return _job_registry


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", status_code=201)
def register_job_endpoint(
    payload: dict = Body(default={}),
    registry: JobRegistry = Depends(get_job_registry),
) -> dict:
    try:
        job = registry.register(
            payload.get("job_id", ""),
            payload.get("job_type", ""),
            JobMetadata.from_dict(payload.get("metadata")),
        )
    except JobAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return job.to_dict()


@router.get("")
def list_jobs_endpoint(
    tag: Optional[str] = Query(default=None),
    status: Optional[JobStatus] = Query(default=None),
    registry: JobRegistry = Depends(get_job_registry),
) -> list:
    return [job.to_dict() for job in registry.list_jobs(tag=tag, status=status)]


@router.get("/{job}")
def get_job_endpoint(
    job: str,
    registry: JobRegistry = Depends(get_job_registry),
) -> dict:
    try:
        found = registry.get(job)
    except UnknownJobError:
        raise HTTPException(status_code=404, detail="unknown job")
    return found.to_dict()


@router.delete("/{job}", status_code=204)
def remove_job_endpoint(
    job: str,
    registry: JobRegistry = Depends(get_job_registry),
) -> None:
    try:
        registry.remove(job)
    except UnknownJobError:
        raise HTTPException(status_code=404, detail="unknown job")
