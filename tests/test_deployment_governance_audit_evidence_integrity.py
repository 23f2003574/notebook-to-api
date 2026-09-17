from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from backend.observability.deployment_governance_audit_evidence_integrity import (
    GOVERNANCE_AUDIT_EVIDENCE_MANIFEST_SCHEMA_VERSION,
    GovernanceIntegrityAuditEvidenceManifest,
    GovernanceIntegrityAuditEvidenceVerificationStatus,
    build_governance_audit_evidence_manifest,
    calculate_governance_audit_evidence_file_sha256,
    calculate_governance_audit_evidence_sha256,
    default_governance_audit_evidence_manifest_path,
    load_governance_audit_evidence_manifest,
    serialize_governance_audit_evidence_manifest,
    verify_governance_audit_evidence,
    write_governance_audit_evidence_manifest,
)


EXPORTED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def test_evidence_sha256_matches_known_digest() -> None:
    digest = calculate_governance_audit_evidence_sha256(
        b"notebook2api"
    )

    assert digest == hashlib.sha256(b"notebook2api").hexdigest()


def test_file_sha256_matches_bytes_digest(tmp_path) -> None:
    payload = b"governance evidence payload"

    path = tmp_path / "evidence.json"
    path.write_bytes(payload)

    assert (
        calculate_governance_audit_evidence_file_sha256(path)
        == calculate_governance_audit_evidence_sha256(payload)
    )


def test_manifest_describes_written_evidence_file(tmp_path) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b'{"healthy":true}\n')

    manifest = build_governance_audit_evidence_manifest(
        evidence_path=evidence_path,
        record_count=1,
        exported_at=EXPORTED_AT,
    )

    assert manifest.evidence_filename == "evidence.json"
    assert manifest.byte_size == evidence_path.stat().st_size
    assert manifest.sha256 == hashlib.sha256(
        evidence_path.read_bytes()
    ).hexdigest()
    assert manifest.hash_algorithm == "sha256"
    assert manifest.schema_version == (
        GOVERNANCE_AUDIT_EVIDENCE_MANIFEST_SCHEMA_VERSION
    )


def test_manifest_rejects_invalid_sha256_length() -> None:
    with pytest.raises(
        ValueError, match="64 hexadecimal characters"
    ):
        GovernanceIntegrityAuditEvidenceManifest(
            schema_version=1,
            evidence_filename="evidence.json",
            hash_algorithm="sha256",
            sha256="not-long-enough",
            byte_size=10,
            record_count=1,
            exported_at=EXPORTED_AT,
        )


def test_manifest_rejects_non_hexadecimal_sha256() -> None:
    with pytest.raises(ValueError, match="must be hexadecimal"):
        GovernanceIntegrityAuditEvidenceManifest(
            schema_version=1,
            evidence_filename="evidence.json",
            hash_algorithm="sha256",
            sha256="z" * 64,
            byte_size=10,
            record_count=1,
            exported_at=EXPORTED_AT,
        )


def test_manifest_rejects_unsupported_hash_algorithm() -> None:
    with pytest.raises(
        ValueError, match="unsupported evidence hash algorithm"
    ):
        GovernanceIntegrityAuditEvidenceManifest(
            schema_version=1,
            evidence_filename="evidence.json",
            hash_algorithm="md5",
            sha256="a" * 64,
            byte_size=10,
            record_count=1,
            exported_at=EXPORTED_AT,
        )


def test_default_manifest_path_preserves_full_evidence_filename() -> None:
    path = default_governance_audit_evidence_manifest_path(
        "governance-audits.json"
    )

    assert path.name == "governance-audits.json.manifest.json"


def test_manifest_serialization_is_deterministic(tmp_path) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b"payload")

    manifest = build_governance_audit_evidence_manifest(
        evidence_path=evidence_path,
        record_count=3,
        exported_at=EXPORTED_AT,
    )

    first = serialize_governance_audit_evidence_manifest(manifest)
    second = serialize_governance_audit_evidence_manifest(manifest)

    assert first == second

    payload = json.loads(first)
    assert payload["schema_version"] == 1
    assert payload["record_count"] == 3


def test_write_and_load_manifest_round_trip(tmp_path) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b"payload")

    manifest = build_governance_audit_evidence_manifest(
        evidence_path=evidence_path,
        record_count=2,
        exported_at=EXPORTED_AT,
    )

    manifest_path = tmp_path / "evidence.json.manifest.json"

    write_governance_audit_evidence_manifest(
        manifest, manifest_path
    )

    loaded = load_governance_audit_evidence_manifest(manifest_path)

    assert loaded == manifest


def test_write_manifest_refuses_to_overwrite_by_default(
    tmp_path,
) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b"payload")

    manifest = build_governance_audit_evidence_manifest(
        evidence_path=evidence_path,
        record_count=1,
        exported_at=EXPORTED_AT,
    )

    manifest_path = tmp_path / "evidence.json.manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_governance_audit_evidence_manifest(
            manifest, manifest_path
        )


def test_load_manifest_rejects_unsupported_schema_version(
    tmp_path,
) -> None:
    manifest_path = tmp_path / "evidence.json.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 99,
                "evidence_filename": "evidence.json",
                "hash_algorithm": "sha256",
                "sha256": "a" * 64,
                "byte_size": 1,
                "record_count": 1,
                "exported_at": EXPORTED_AT.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError, match="unsupported governance audit evidence"
    ):
        load_governance_audit_evidence_manifest(manifest_path)


def test_verification_passes_for_untouched_evidence(tmp_path) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b"payload")

    manifest = build_governance_audit_evidence_manifest(
        evidence_path=evidence_path,
        record_count=1,
        exported_at=EXPORTED_AT,
    )

    verification = verify_governance_audit_evidence(
        evidence_path=evidence_path, manifest=manifest
    )

    assert verification.verified is True
    assert (
        verification.status
        is GovernanceIntegrityAuditEvidenceVerificationStatus.VERIFIED
    )


def test_verification_detects_size_change(tmp_path) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b"payload")

    manifest = build_governance_audit_evidence_manifest(
        evidence_path=evidence_path,
        record_count=1,
        exported_at=EXPORTED_AT,
    )

    evidence_path.write_bytes(b"payload-with-more-bytes")

    verification = verify_governance_audit_evidence(
        evidence_path=evidence_path, manifest=manifest
    )

    assert verification.verified is False
    assert (
        verification.status
        is (
            GovernanceIntegrityAuditEvidenceVerificationStatus
            .SIZE_MISMATCH
        )
    )


def test_verification_detects_same_size_content_tampering(
    tmp_path,
) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b"healthy")

    manifest = build_governance_audit_evidence_manifest(
        evidence_path=evidence_path,
        record_count=1,
        exported_at=EXPORTED_AT,
    )

    evidence_path.write_bytes(b"corrupt")  # same length, 7 bytes

    verification = verify_governance_audit_evidence(
        evidence_path=evidence_path, manifest=manifest
    )

    assert verification.verified is False
    assert (
        verification.status
        is (
            GovernanceIntegrityAuditEvidenceVerificationStatus
            .DIGEST_MISMATCH
        )
    )


def test_verification_reports_missing_evidence_file(tmp_path) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b"payload")

    manifest = build_governance_audit_evidence_manifest(
        evidence_path=evidence_path,
        record_count=1,
        exported_at=EXPORTED_AT,
    )

    verification = verify_governance_audit_evidence(
        evidence_path=tmp_path / "missing.json", manifest=manifest
    )

    assert verification.verified is False
    assert (
        verification.status
        is (
            GovernanceIntegrityAuditEvidenceVerificationStatus
            .EVIDENCE_FILE_MISSING
        )
    )
