from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .object_storage import ObjectStorageEngine, StorageObject, get_object_storage_engine


class ChecksumAlgorithm(str, Enum):
    """Algorithms the integrity engine can use to fingerprint object data."""

    SHA256 = "sha256"
    SHA512 = "sha512"
    MD5 = "md5"
    CRC32 = "crc32"


@dataclass(frozen=True)
class ChecksumRecord:
    """A recorded checksum baseline for a key under a given algorithm."""

    key: str
    algorithm: ChecksumAlgorithm
    checksum: str
    computed_at: datetime

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "algorithm": self.algorithm.value,
            "checksum": self.checksum,
            "computed_at": self.computed_at.isoformat(),
        }


@dataclass
class IntegrityReport:
    """The outcome of verifying a stored object against an expected checksum."""

    key: str
    algorithm: ChecksumAlgorithm
    expected_checksum: Optional[str]
    actual_checksum: Optional[str]
    verified: bool
    status: str
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "algorithm": self.algorithm.value,
            "expected_checksum": self.expected_checksum,
            "actual_checksum": self.actual_checksum,
            "verified": self.verified,
            "status": self.status,
            "checked_at": self.checked_at.isoformat(),
        }


class ArtifactIntegrityEngine:
    """Generates checksums and verifies stored objects for corruption."""

    def __init__(self, *, object_storage: ObjectStorageEngine, auto_record: bool = True) -> None:
        self._object_storage = object_storage
        self._ledger: dict = {}
        self._lock = Lock()
        if auto_record:
            object_storage.add_listener(self._on_object_stored)

    def calculate_checksum(self, data: bytes, *, algorithm: ChecksumAlgorithm = ChecksumAlgorithm.SHA256) -> str:
        if algorithm == ChecksumAlgorithm.SHA256:
            return hashlib.sha256(data).hexdigest()
        if algorithm == ChecksumAlgorithm.SHA512:
            return hashlib.sha512(data).hexdigest()
        if algorithm == ChecksumAlgorithm.MD5:
            return hashlib.md5(data).hexdigest()
        if algorithm == ChecksumAlgorithm.CRC32:
            return format(zlib.crc32(data) & 0xFFFFFFFF, "08x")
        raise ValueError(f"unsupported algorithm '{algorithm}'")

    def verify(
        self,
        key: str,
        *,
        algorithm: ChecksumAlgorithm = ChecksumAlgorithm.SHA256,
        expected_checksum: Optional[str] = None,
    ) -> IntegrityReport:
        now = datetime.now(timezone.utc)
        obj = self._object_storage.get(key)
        if obj is None:
            return IntegrityReport(
                key=key,
                algorithm=algorithm,
                expected_checksum=expected_checksum,
                actual_checksum=None,
                verified=False,
                status="missing",
                checked_at=now,
            )

        actual = self.calculate_checksum(obj.data, algorithm=algorithm)
        if expected_checksum is None:
            expected_checksum = self._baseline(key, algorithm)
            if expected_checksum is None:
                if algorithm == ChecksumAlgorithm.SHA256:
                    expected_checksum = obj.metadata.checksum
                else:
                    self._record(key, algorithm, actual)
                    expected_checksum = actual

        verified = actual == expected_checksum
        return IntegrityReport(
            key=key,
            algorithm=algorithm,
            expected_checksum=expected_checksum,
            actual_checksum=actual,
            verified=verified,
            status="ok" if verified else "corrupted",
            checked_at=now,
        )

    def repair_metadata(self, key: str) -> IntegrityReport:
        """Recompute and re-record every tracked checksum baseline for a key from its current data."""
        obj = self._object_storage.get(key)
        if obj is None:
            raise KeyError(key)

        with self._lock:
            tracked_algorithms = set(self._ledger.get(key, {}))
        tracked_algorithms.add(ChecksumAlgorithm.SHA256)

        for algorithm in tracked_algorithms:
            recomputed = self.calculate_checksum(obj.data, algorithm=algorithm)
            self._record(key, algorithm, recomputed)

        sha256_checksum = self.calculate_checksum(obj.data, algorithm=ChecksumAlgorithm.SHA256)
        return IntegrityReport(
            key=key,
            algorithm=ChecksumAlgorithm.SHA256,
            expected_checksum=sha256_checksum,
            actual_checksum=sha256_checksum,
            verified=True,
            status="ok",
        )

    def audit(self, *, keys: Optional[list] = None, algorithm: ChecksumAlgorithm = ChecksumAlgorithm.SHA256) -> list:
        with self._lock:
            target_keys = list(keys) if keys is not None else list(self._ledger.keys())
        return [self.verify(key, algorithm=algorithm) for key in target_keys]

    def _on_object_stored(self, obj: StorageObject) -> None:
        self._record(obj.key, ChecksumAlgorithm.SHA256, obj.metadata.checksum)

    def _record(self, key: str, algorithm: ChecksumAlgorithm, checksum: str) -> None:
        with self._lock:
            self._ledger.setdefault(key, {})[algorithm] = checksum

    def _baseline(self, key: str, algorithm: ChecksumAlgorithm) -> Optional[str]:
        with self._lock:
            return self._ledger.get(key, {}).get(algorithm)


_artifact_integrity_engine = ArtifactIntegrityEngine(object_storage=get_object_storage_engine())


def get_artifact_integrity_engine() -> ArtifactIntegrityEngine:
    return _artifact_integrity_engine


router = APIRouter(prefix="/storage/integrity", tags=["artifact-integrity"])


@router.post("/verify")
def verify_endpoint(
    key: str,
    algorithm: ChecksumAlgorithm = ChecksumAlgorithm.SHA256,
    expected_checksum: Optional[str] = None,
    engine: ArtifactIntegrityEngine = Depends(get_artifact_integrity_engine),
) -> dict:
    report = engine.verify(key, algorithm=algorithm, expected_checksum=expected_checksum)
    return report.to_dict()


@router.get("/{artifact:path}")
def get_integrity_endpoint(
    artifact: str,
    algorithm: ChecksumAlgorithm = ChecksumAlgorithm.SHA256,
    engine: ArtifactIntegrityEngine = Depends(get_artifact_integrity_engine),
) -> dict:
    report = engine.verify(artifact, algorithm=algorithm)
    if report.status == "missing":
        raise HTTPException(status_code=404, detail=f"object '{artifact}' not found")
    return report.to_dict()


@router.post("/audit")
def audit_endpoint(
    keys: Optional[str] = None,
    algorithm: ChecksumAlgorithm = ChecksumAlgorithm.SHA256,
    engine: ArtifactIntegrityEngine = Depends(get_artifact_integrity_engine),
) -> list:
    key_list = keys.split(",") if keys else None
    reports = engine.audit(keys=key_list, algorithm=algorithm)
    return [report.to_dict() for report in reports]
