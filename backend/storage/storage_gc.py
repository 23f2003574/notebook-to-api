from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .artifact_manager import ArtifactManager, get_artifact_manager
from .object_storage import ObjectStorageEngine, get_object_storage_engine
from .storage_versioning import StorageVersionManager, get_storage_version_manager


class CleanupTarget(str, Enum):
    """The categories of reclaimable storage the collector understands."""

    ORPHANED_OBJECT = "orphaned_object"
    EXPIRED_VERSION = "expired_version"
    TEMPORARY_UPLOAD = "temporary_upload"
    FAILED_ARTIFACT = "failed_artifact"


@dataclass
class GarbageCandidate:
    """A single object identified as reclaimable during a scan."""

    candidate_id: str
    key: str
    target: CleanupTarget
    size: int
    reason: str
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "key": self.key,
            "target": self.target.value,
            "size": self.size,
            "reason": self.reason,
            "discovered_at": self.discovered_at.isoformat(),
        }


@dataclass
class CleanupReport:
    """The outcome of a sweep, dry-run or otherwise."""

    report_id: str
    dry_run: bool
    candidates_found: int
    objects_removed: int
    bytes_reclaimed: int
    by_target: dict
    started_at: datetime
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "dry_run": self.dry_run,
            "candidates_found": self.candidates_found,
            "objects_removed": self.objects_removed,
            "bytes_reclaimed": self.bytes_reclaimed,
            "by_target": dict(self.by_target),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }


class StorageGarbageCollector:
    """Finds and reclaims objects no longer referenced by any live artifact."""

    def __init__(
        self,
        *,
        object_storage: ObjectStorageEngine,
        artifact_manager: ArtifactManager,
        version_manager: Optional[StorageVersionManager] = None,
    ) -> None:
        self._object_storage = object_storage
        self._artifact_manager = artifact_manager
        self._version_manager = version_manager
        self._extra_providers: list = []
        self._last_scan: Optional[list] = None
        self._marked: Optional[list] = None
        self._last_report: Optional[CleanupReport] = None
        self._lock = Lock()

    def add_candidate_provider(self, provider) -> None:
        """Register an extra callable returning GarbageCandidate objects, e.g. for temporary uploads."""
        with self._lock:
            self._extra_providers.append(provider)

    def scan(self) -> list:
        artifacts = self._artifact_manager.list_artifacts()
        claimed = {artifact.object_key for artifact in artifacts if artifact.status != "failed"}
        candidates = []

        if self._version_manager is not None:
            for artifact in artifacts:
                for version in self._version_manager.history(artifact.artifact_id):
                    if version.object_key in claimed:
                        continue
                    candidates.append(
                        self._candidate(
                            version.object_key,
                            CleanupTarget.EXPIRED_VERSION,
                            f"superseded version {version.version_number} of artifact '{artifact.artifact_id}'",
                        )
                    )
                    claimed.add(version.object_key)

        for artifact in artifacts:
            if artifact.status == "failed" and artifact.object_key not in claimed:
                candidates.append(
                    self._candidate(
                        artifact.object_key,
                        CleanupTarget.FAILED_ARTIFACT,
                        f"artifact '{artifact.artifact_id}' marked failed",
                    )
                )
                claimed.add(artifact.object_key)

        for key in self._object_storage.list_keys():
            if key in claimed:
                continue
            candidates.append(
                self._candidate(key, CleanupTarget.ORPHANED_OBJECT, "not referenced by any artifact or version")
            )
            claimed.add(key)

        with self._lock:
            providers = list(self._extra_providers)
        for provider in providers:
            candidates.extend(provider())

        with self._lock:
            self._last_scan = list(candidates)
        return candidates

    def mark(self, candidate_ids: Optional[list] = None) -> list:
        with self._lock:
            if self._last_scan is None:
                raise ValueError("scan() must be run before mark()")
            scanned = {candidate.candidate_id: candidate for candidate in self._last_scan}

        if candidate_ids is None:
            marked = list(scanned.values())
        else:
            marked = []
            for candidate_id in candidate_ids:
                if candidate_id not in scanned:
                    raise ValueError(f"candidate '{candidate_id}' was not found in the last scan")
                marked.append(scanned[candidate_id])

        with self._lock:
            self._marked = marked
        return marked

    def sweep(self, *, dry_run: bool = False, candidates: Optional[list] = None) -> CleanupReport:
        with self._lock:
            targets = list(candidates) if candidates is not None else self._marked
        if targets is None:
            raise ValueError("mark() must be run before sweep(), or pass candidates explicitly")

        started_at = datetime.now(timezone.utc)
        by_target: dict = {}
        objects_removed = 0
        bytes_reclaimed = 0

        for candidate in targets:
            by_target[candidate.target.value] = by_target.get(candidate.target.value, 0) + 1
            if not dry_run and self._object_storage.delete(candidate.key):
                objects_removed += 1
                bytes_reclaimed += candidate.size

        report = CleanupReport(
            report_id=uuid.uuid4().hex,
            dry_run=dry_run,
            candidates_found=len(targets),
            objects_removed=objects_removed,
            bytes_reclaimed=bytes_reclaimed,
            by_target=by_target,
            started_at=started_at,
        )
        with self._lock:
            self._last_report = report
            if not dry_run:
                self._marked = None
                self._last_scan = None
        return report

    def run(self, *, dry_run: bool = False) -> CleanupReport:
        self.scan()
        self.mark()
        return self.sweep(dry_run=dry_run)

    def report(self) -> Optional[CleanupReport]:
        with self._lock:
            return self._last_report

    def _candidate(self, key: str, target: CleanupTarget, reason: str) -> GarbageCandidate:
        obj = self._object_storage.get(key)
        return GarbageCandidate(
            candidate_id=uuid.uuid4().hex,
            key=key,
            target=target,
            size=obj.metadata.size if obj is not None else 0,
            reason=reason,
        )


_storage_garbage_collector = StorageGarbageCollector(
    object_storage=get_object_storage_engine(),
    artifact_manager=get_artifact_manager(),
    version_manager=get_storage_version_manager(),
)


def get_storage_garbage_collector() -> StorageGarbageCollector:
    return _storage_garbage_collector


router = APIRouter(prefix="/storage/gc", tags=["storage-gc"])


@router.post("/run")
def run_endpoint(
    gc: StorageGarbageCollector = Depends(get_storage_garbage_collector),
) -> dict:
    return gc.run(dry_run=False).to_dict()


@router.post("/dry-run")
def dry_run_endpoint(
    gc: StorageGarbageCollector = Depends(get_storage_garbage_collector),
) -> dict:
    return gc.run(dry_run=True).to_dict()


@router.get("/report")
def report_endpoint(
    gc: StorageGarbageCollector = Depends(get_storage_garbage_collector),
) -> dict:
    report = gc.report()
    if report is None:
        raise HTTPException(status_code=404, detail="no cleanup report available yet")
    return report.to_dict()


@router.get("/candidates")
def candidates_endpoint(
    gc: StorageGarbageCollector = Depends(get_storage_garbage_collector),
) -> list:
    return [candidate.to_dict() for candidate in gc.scan()]
