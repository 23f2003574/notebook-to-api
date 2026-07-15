from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


GOVERNANCE_AUDIT_EVIDENCE_MANIFEST_SCHEMA_VERSION = 1

GOVERNANCE_AUDIT_EVIDENCE_HASH_ALGORITHM = "sha256"

_FILE_READ_CHUNK_SIZE = 64 * 1024


def calculate_governance_audit_evidence_sha256(payload: bytes) -> str:
    """
    Calculate the lowercase hexadecimal SHA-256 digest of governance
    evidence bytes.

    Accepts raw bytes only (not a bundle object, dict, or Path): the rule
    is always to hash exactly the bytes that were written to disk.
    """

    return hashlib.sha256(payload).hexdigest()


def calculate_governance_audit_evidence_file_sha256(
    path: str | Path,
) -> str:
    """
    Calculate the SHA-256 digest of an evidence file, streaming it in
    chunks rather than loading the whole file into memory at once.
    """

    evidence_path = Path(path)

    hasher = hashlib.sha256()

    with evidence_path.open("rb") as handle:
        while True:
            chunk = handle.read(_FILE_READ_CHUNK_SIZE)

            if not chunk:
                break

            hasher.update(chunk)

    return hasher.hexdigest()


@dataclass(frozen=True)
class GovernanceIntegrityAuditEvidenceManifest:
    """
    Tamper-evident metadata for one exported evidence file.

    Provides accidental-corruption and post-export-modification detection
    only. It does not prove author identity or provide non-repudiation: an
    attacker who can replace the evidence file can equally recompute and
    replace this manifest. That would require a digital signature over a
    trusted key, which is a deliberately separate, later concern.
    """

    schema_version: int

    evidence_filename: str

    hash_algorithm: str

    sha256: str

    byte_size: int

    record_count: int

    exported_at: datetime

    def __post_init__(self) -> None:
        if self.schema_version <= 0:
            raise ValueError(
                "schema_version must be greater than zero"
            )

        if not self.evidence_filename:
            raise ValueError(
                "evidence_filename must not be empty"
            )

        if (
            self.hash_algorithm
            != GOVERNANCE_AUDIT_EVIDENCE_HASH_ALGORITHM
        ):
            raise ValueError(
                "unsupported evidence hash algorithm"
            )

        if len(self.sha256) != 64:
            raise ValueError(
                "sha256 must contain 64 hexadecimal characters"
            )

        try:
            int(self.sha256, 16)

        except ValueError as exc:
            raise ValueError(
                "sha256 must be hexadecimal"
            ) from exc

        if self.byte_size < 0:
            raise ValueError(
                "byte_size must not be negative"
            )

        if self.record_count < 0:
            raise ValueError(
                "record_count must not be negative"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "evidence_filename": self.evidence_filename,
            "hash_algorithm": self.hash_algorithm,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "record_count": self.record_count,
            "exported_at": self.exported_at.isoformat(),
        }


def build_governance_audit_evidence_manifest(
    *,
    evidence_path: str | Path,
    record_count: int,
    exported_at: datetime,
) -> GovernanceIntegrityAuditEvidenceManifest:
    """
    Build a manifest describing an already-written evidence file.

    Must be called after the evidence file is completely written: hashing
    a serialized string and then writing the file differently (even a
    trailing newline) would produce a manifest that describes bytes that
    were never actually persisted.
    """

    path = Path(evidence_path)

    return GovernanceIntegrityAuditEvidenceManifest(
        schema_version=(
            GOVERNANCE_AUDIT_EVIDENCE_MANIFEST_SCHEMA_VERSION
        ),
        evidence_filename=path.name,
        hash_algorithm=GOVERNANCE_AUDIT_EVIDENCE_HASH_ALGORITHM,
        sha256=calculate_governance_audit_evidence_file_sha256(path),
        byte_size=path.stat().st_size,
        record_count=record_count,
        exported_at=exported_at,
    )


def serialize_governance_audit_evidence_manifest(
    manifest: GovernanceIntegrityAuditEvidenceManifest,
    *,
    pretty: bool = True,
) -> str:
    return json.dumps(
        manifest.to_dict(),
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
        separators=None if pretty else (",", ":"),
    )


def default_governance_audit_evidence_manifest_path(
    evidence_path: str | Path,
) -> Path:
    """
    governance-audits.json -> governance-audits.json.manifest.json

    Appending rather than replacing the suffix keeps the manifest
    filename unambiguously tied to the complete evidence filename.
    """

    path = Path(evidence_path)

    return path.with_name(path.name + ".manifest.json")


def write_governance_audit_evidence_manifest(
    manifest: GovernanceIntegrityAuditEvidenceManifest,
    output_path: str | Path,
    *,
    pretty: bool = True,
    overwrite: bool = False,
) -> Path:
    path = Path(output_path)

    if path.exists() and not overwrite:
        raise FileExistsError(
            f"manifest file already exists: {path}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    payload = serialize_governance_audit_evidence_manifest(
        manifest, pretty=pretty
    )

    path.write_text(payload + "\n", encoding="utf-8")

    return path


def load_governance_audit_evidence_manifest(
    path: str | Path,
) -> GovernanceIntegrityAuditEvidenceManifest:
    manifest_path = Path(path)

    payload = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    schema_version = int(payload["schema_version"])

    if (
        schema_version
        != GOVERNANCE_AUDIT_EVIDENCE_MANIFEST_SCHEMA_VERSION
    ):
        raise ValueError(
            "unsupported governance audit evidence "
            f"manifest schema version: {schema_version}"
        )

    return GovernanceIntegrityAuditEvidenceManifest(
        schema_version=schema_version,
        evidence_filename=str(payload["evidence_filename"]),
        hash_algorithm=str(payload["hash_algorithm"]),
        sha256=str(payload["sha256"]),
        byte_size=int(payload["byte_size"]),
        record_count=int(payload["record_count"]),
        exported_at=datetime.fromisoformat(
            str(payload["exported_at"])
        ),
    )


class GovernanceIntegrityAuditEvidenceVerificationStatus(
    str,
    Enum,
):
    VERIFIED = "verified"

    DIGEST_MISMATCH = "digest_mismatch"

    SIZE_MISMATCH = "size_mismatch"

    EVIDENCE_FILE_MISSING = "evidence_file_missing"


@dataclass(frozen=True)
class GovernanceIntegrityAuditEvidenceVerificationResult:
    """
    Result of verifying an evidence file against its manifest.
    """

    status: GovernanceIntegrityAuditEvidenceVerificationStatus

    verified: bool

    expected_sha256: str

    actual_sha256: str | None

    expected_byte_size: int

    actual_byte_size: int | None

    def __post_init__(self) -> None:
        expected_verified = (
            self.status
            is (
                GovernanceIntegrityAuditEvidenceVerificationStatus
                .VERIFIED
            )
        )

        if self.verified != expected_verified:
            raise ValueError(
                "verified must match verification status"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "verified": self.verified,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "expected_byte_size": self.expected_byte_size,
            "actual_byte_size": self.actual_byte_size,
        }


def verify_governance_audit_evidence(
    *,
    evidence_path: str | Path,
    manifest: GovernanceIntegrityAuditEvidenceManifest,
) -> GovernanceIntegrityAuditEvidenceVerificationResult:
    path = Path(evidence_path)

    if not path.exists():
        return GovernanceIntegrityAuditEvidenceVerificationResult(
            status=(
                GovernanceIntegrityAuditEvidenceVerificationStatus
                .EVIDENCE_FILE_MISSING
            ),
            verified=False,
            expected_sha256=manifest.sha256,
            actual_sha256=None,
            expected_byte_size=manifest.byte_size,
            actual_byte_size=None,
        )

    actual_byte_size = path.stat().st_size

    if actual_byte_size != manifest.byte_size:
        return GovernanceIntegrityAuditEvidenceVerificationResult(
            status=(
                GovernanceIntegrityAuditEvidenceVerificationStatus
                .SIZE_MISMATCH
            ),
            verified=False,
            expected_sha256=manifest.sha256,
            actual_sha256=(
                calculate_governance_audit_evidence_file_sha256(path)
            ),
            expected_byte_size=manifest.byte_size,
            actual_byte_size=actual_byte_size,
        )

    actual_sha256 = calculate_governance_audit_evidence_file_sha256(
        path
    )

    if actual_sha256 != manifest.sha256:
        return GovernanceIntegrityAuditEvidenceVerificationResult(
            status=(
                GovernanceIntegrityAuditEvidenceVerificationStatus
                .DIGEST_MISMATCH
            ),
            verified=False,
            expected_sha256=manifest.sha256,
            actual_sha256=actual_sha256,
            expected_byte_size=manifest.byte_size,
            actual_byte_size=actual_byte_size,
        )

    return GovernanceIntegrityAuditEvidenceVerificationResult(
        status=(
            GovernanceIntegrityAuditEvidenceVerificationStatus.VERIFIED
        ),
        verified=True,
        expected_sha256=manifest.sha256,
        actual_sha256=actual_sha256,
        expected_byte_size=manifest.byte_size,
        actual_byte_size=actual_byte_size,
    )
