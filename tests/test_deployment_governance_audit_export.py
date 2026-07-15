from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_export import (
    GOVERNANCE_AUDIT_EVIDENCE_SCHEMA_VERSION,
    GovernanceIntegrityAuditExportOptions,
    GovernanceIntegrityAuditExportService,
    serialize_governance_integrity_audit_evidence,
)
from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_regression import (
    GovernanceIntegrityRegressionStatus,
)
from backend.observability.deployment_governance_audit_trends import (
    GovernanceIntegrityAuditTrendDirection,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)


EXPORTED_AT = datetime(
    2026,
    7,
    15,
    12,
    0,
    tzinfo=timezone.utc,
)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    invalid_records: int = 0,
) -> GovernanceIntegrityAuditRecord:
    started_at = EXPORTED_AT + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if invalid_records == 0
            else GovernanceIntegrityAuditOutcome.UNHEALTHY
        ),
        total_records=10,
        valid_records=10 - invalid_records,
        invalid_records=invalid_records,
        integrity_mismatches=invalid_records,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )


def make_export_service(
    repository, *, clock=None
) -> GovernanceIntegrityAuditExportService:
    return GovernanceIntegrityAuditExportService(
        repository=repository,
        clock=clock or (lambda: EXPORTED_AT),
    )


def test_export_builds_valid_bundle_for_empty_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_export_service(repository)

    bundle = service.build_bundle()

    assert bundle.schema_version == GOVERNANCE_AUDIT_EVIDENCE_SCHEMA_VERSION
    assert bundle.record_count == 0
    assert bundle.records == ()
    assert bundle.summary.total_audits == 0
    assert bundle.summary.newest_audit_id is None

    assert bundle.trend is not None
    assert (
        bundle.trend.direction
        is GovernanceIntegrityAuditTrendDirection.INSUFFICIENT_DATA
    )

    assert bundle.regression is not None
    assert (
        bundle.regression.status
        is GovernanceIntegrityRegressionStatus.NO_HISTORY
    )


def test_export_preserves_newest_first_record_order() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="old", offset_minutes=0))
    repository.save(make_record(audit_id="new", offset_minutes=10))

    bundle = make_export_service(repository).build_bundle()

    assert [record.audit_id for record in bundle.records] == [
        "new",
        "old",
    ]


def test_export_limit_selects_only_recent_records() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    for index in range(5):
        repository.save(
            make_record(
                audit_id=f"audit-{index}", offset_minutes=index
            )
        )

    service = make_export_service(repository)

    bundle = service.build_bundle(
        GovernanceIntegrityAuditExportOptions(limit=2)
    )

    assert bundle.record_count == 2
    assert bundle.trend is not None
    assert bundle.trend.sample_size <= 2


def test_export_can_omit_trend_and_regression_analysis() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="audit-1"))

    service = make_export_service(repository)

    bundle = service.build_bundle(
        GovernanceIntegrityAuditExportOptions(
            include_trend=False, include_regression=False
        )
    )

    assert bundle.trend is None
    assert bundle.regression is None


def test_export_rejects_non_positive_limit() -> None:
    with pytest.raises(
        ValueError, match="limit must be greater than zero"
    ):
        GovernanceIntegrityAuditExportOptions(limit=0)


def test_export_rejects_non_positive_trend_window() -> None:
    with pytest.raises(
        ValueError, match="trend_window must be greater than zero"
    ):
        GovernanceIntegrityAuditExportOptions(trend_window=0)


def test_bundle_self_consistency_with_limited_export() -> None:
    # The critical property: a bundle exported with --limit N must never
    # claim analysis over more than N records, even though the full
    # repository contains more.
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record(
            audit_id="baseline-healthy",
            offset_minutes=0,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="middle-healthy",
            offset_minutes=10,
            invalid_records=0,
        )
    )

    repository.save(
        make_record(
            audit_id="latest-unhealthy",
            offset_minutes=20,
            invalid_records=1,
        )
    )

    service = make_export_service(repository)

    bundle = service.build_bundle(
        GovernanceIntegrityAuditExportOptions(limit=1)
    )

    assert bundle.record_count == 1
    assert bundle.trend.sample_size == 1

    # Regression cannot be established from a single-record export even
    # though the full repository has a healthy baseline for this audit.
    assert (
        bundle.regression.status
        is GovernanceIntegrityRegressionStatus.INSUFFICIENT_BASELINE
    )


def test_evidence_serialization_is_deterministic() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="audit-1"))

    bundle = make_export_service(repository).build_bundle()

    first = serialize_governance_integrity_audit_evidence(bundle)
    second = serialize_governance_integrity_audit_evidence(bundle)

    assert first == second

    payload = json.loads(first)

    assert payload["schema_version"] == 1


def test_evidence_serialization_compact_mode() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    bundle = make_export_service(repository).build_bundle()

    compact = serialize_governance_integrity_audit_evidence(
        bundle, pretty=False
    )

    assert "\n" not in compact
    assert json.loads(compact)["schema_version"] == 1


def test_export_writes_utf8_json_file(tmp_path) -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="audit-1"))

    service = make_export_service(repository)

    output_path = tmp_path / "evidence.json"

    result = service.export_to_file(output_path)

    assert output_path.exists()

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["record_count"] == result.bundle.record_count
    assert len(payload["records"]) == 1
    assert "summary" in payload
    assert "trend" in payload
    assert "regression" in payload


def test_export_refuses_to_overwrite_existing_file_by_default(
    tmp_path,
) -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_export_service(repository)

    output_path = tmp_path / "evidence.json"
    output_path.write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError):
        service.export_to_file(output_path)


def test_export_can_overwrite_when_explicitly_enabled(tmp_path) -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_export_service(repository)

    output_path = tmp_path / "evidence.json"
    output_path.write_text("{}", encoding="utf-8")

    service.export_to_file(output_path, overwrite=True)

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1


def test_export_creates_evidence_and_manifest_files_by_default(
    tmp_path,
) -> None:
    from backend.observability.deployment_governance_audit_evidence_integrity import (
        verify_governance_audit_evidence,
    )

    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="audit-1"))

    service = make_export_service(repository)

    output_path = tmp_path / "evidence.json"

    result = service.export_to_file(output_path)

    assert result.evidence_path.exists()
    assert result.manifest is not None
    assert result.manifest_path is not None
    assert result.manifest_path.exists()
    assert (
        result.manifest_path.name
        == "evidence.json.manifest.json"
    )

    verification = verify_governance_audit_evidence(
        evidence_path=result.evidence_path,
        manifest=result.manifest,
    )

    assert verification.verified is True


def test_export_can_disable_manifest_creation(tmp_path) -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = make_export_service(repository)

    output_path = tmp_path / "evidence.json"

    result = service.export_to_file(
        output_path,
        options=GovernanceIntegrityAuditExportOptions(
            create_manifest=False
        ),
    )

    assert result.manifest is None
    assert result.manifest_path is None
    assert not (tmp_path / "evidence.json.manifest.json").exists()


def test_sqlite_audit_history_can_be_exported_as_portable_json(
    tmp_path,
) -> None:
    database_path = tmp_path / "governance.db"
    output_path = tmp_path / "evidence.json"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    recording_service = (
        runtime.build_integrity_audit_recording_service()
    )

    recording_service.audit_and_record()
    recording_service.audit_and_record()

    result = (
        runtime.build_integrity_audit_export_service().export_to_file(
            output_path
        )
    )

    assert result.bundle.record_count == 2

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert len(payload["records"]) == 2
    assert "summary" in payload
    assert "trend" in payload
    assert "regression" in payload

    from backend.observability.deployment_governance_audit_evidence_integrity import (
        load_governance_audit_evidence_manifest,
        verify_governance_audit_evidence,
    )

    assert result.manifest_path is not None

    loaded_manifest = load_governance_audit_evidence_manifest(
        result.manifest_path
    )

    verification = verify_governance_audit_evidence(
        evidence_path=output_path, manifest=loaded_manifest
    )

    assert verification.verified is True
