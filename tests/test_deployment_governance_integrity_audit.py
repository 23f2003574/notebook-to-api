from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.observability.deployment_governance_integrity_audit import (
    DeploymentGovernanceIntegrityAuditService,
    GovernanceTraceIntegrityAuditStatus,
)
from backend.observability.deployment_governance_trace_repository import (
    GovernanceTraceRecord,
)
from backend.observability.sqlite_deployment_governance_trace_repository import (
    SQLiteDeploymentGovernanceTraceRepository,
)
from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLiteDatabaseConfig,
)


def make_record(
    *,
    trace_id: str,
    deployment_id: str,
) -> GovernanceTraceRecord:
    timestamp = datetime(
        2026,
        7,
        14,
        14,
        0,
        0,
        tzinfo=timezone.utc,
    )

    return GovernanceTraceRecord(
        trace_id=trace_id,
        deployment_id=deployment_id,
        service_name="payments-api",
        environment="production",
        artifact_digest=(
            f"sha256:{trace_id}"
        ),
        created_at=timestamp,
        updated_at=timestamp,
        governance_state="created",
        final_status=None,
        completed=False,
        payload={
            "schema_version": 1,
            "trace": {
                "trace_id": trace_id,
                "deployment_id": deployment_id,
            },
            "events": [],
        },
    )


def build_repository(
    tmp_path: Path,
    filename: str,
) -> tuple[
    SQLiteDatabase,
    SQLiteDeploymentGovernanceTraceRepository,
]:
    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=(
                tmp_path
                / filename
            ),
        )
    )

    repository = (
        SQLiteDeploymentGovernanceTraceRepository(
            database
        )
    )

    return (
        database,
        repository,
    )


def test_empty_repository_produces_healthy_audit(
    tmp_path: Path,
) -> None:
    _, repository = build_repository(
        tmp_path,
        "empty-audit.db",
    )

    service = (
        DeploymentGovernanceIntegrityAuditService(
            repository
        )
    )

    report = service.audit()

    assert report.total_records == 0
    assert report.valid_records == 0
    assert report.invalid_records == 0
    assert report.healthy is True
    assert report.findings == ()


def test_valid_records_produce_healthy_audit(
    tmp_path: Path,
) -> None:
    _, repository = build_repository(
        tmp_path,
        "valid-audit.db",
    )

    repository.save_many(
        (
            make_record(
                trace_id="trace-valid-001",
                deployment_id="deployment-valid-001",
            ),
            make_record(
                trace_id="trace-valid-002",
                deployment_id="deployment-valid-002",
            ),
            make_record(
                trace_id="trace-valid-003",
                deployment_id="deployment-valid-003",
            ),
        )
    )

    report = (
        DeploymentGovernanceIntegrityAuditService(
            repository
        )
        .audit()
    )

    assert report.total_records == 3
    assert report.valid_records == 3
    assert report.invalid_records == 0
    assert report.healthy is True

    assert all(
        finding.status
        is GovernanceTraceIntegrityAuditStatus.VALID
        for finding in report.findings
    )


def test_audit_reports_integrity_mismatch_and_continues(
    tmp_path: Path,
) -> None:
    database, repository = build_repository(
        tmp_path,
        "mismatch-audit.db",
    )

    repository.save_many(
        (
            make_record(
                trace_id="trace-valid-before",
                deployment_id="deployment-valid-before",
            ),
            make_record(
                trace_id="trace-corrupted",
                deployment_id="deployment-corrupted",
            ),
            make_record(
                trace_id="trace-valid-after",
                deployment_id="deployment-valid-after",
            ),
        )
    )

    with database.transaction(
        immediate=True
    ) as connection:
        connection.execute(
            """
            UPDATE
                deployment_governance_traces
            SET
                environment = ?
            WHERE
                trace_id = ?
            """,
            (
                "staging",
                "trace-corrupted",
            ),
        )

    report = (
        DeploymentGovernanceIntegrityAuditService(
            repository
        )
        .audit()
    )

    assert report.total_records == 3
    assert report.valid_records == 2
    assert report.invalid_records == 1
    assert report.integrity_mismatches == 1
    assert report.healthy is False

    mismatch_findings = (
        report.findings_for_status(
            GovernanceTraceIntegrityAuditStatus
            .INTEGRITY_MISMATCH
        )
    )

    assert len(
        mismatch_findings
    ) == 1

    assert (
        mismatch_findings[0].trace_id
        == "trace-corrupted"
    )


def test_audit_reports_missing_integrity_metadata(
    tmp_path: Path,
) -> None:
    database, repository = build_repository(
        tmp_path,
        "missing-integrity-audit.db",
    )

    record = make_record(
        trace_id="trace-missing-integrity",
        deployment_id="deployment-missing-integrity",
    )

    repository.save(
        record
    )

    with database.transaction(
        immediate=True
    ) as connection:
        connection.execute(
            """
            UPDATE
                deployment_governance_traces
            SET
                integrity_algorithm = NULL,
                integrity_version = NULL,
                integrity_digest = NULL
            WHERE
                trace_id = ?
            """,
            (
                record.trace_id,
            ),
        )

    report = (
        DeploymentGovernanceIntegrityAuditService(
            repository
        )
        .audit()
    )

    assert report.total_records == 1
    assert report.valid_records == 0
    assert report.invalid_records == 1

    assert (
        report.missing_integrity_metadata
        == 1
    )

    assert report.healthy is False


def test_audit_reports_invalid_integrity_metadata(
    tmp_path: Path,
) -> None:
    database, repository = build_repository(
        tmp_path,
        "invalid-integrity-metadata.db",
    )

    record = make_record(
        trace_id="trace-invalid-integrity",
        deployment_id="deployment-invalid-integrity",
    )

    repository.save(
        record
    )

    with database.transaction(
        immediate=True
    ) as connection:
        connection.execute(
            """
            UPDATE
                deployment_governance_traces
            SET
                integrity_digest = ?
            WHERE
                trace_id = ?
            """,
            (
                "not-a-valid-sha256-digest",
                record.trace_id,
            ),
        )

    report = (
        DeploymentGovernanceIntegrityAuditService(
            repository
        )
        .audit()
    )

    assert report.total_records == 1

    assert (
        report.invalid_integrity_metadata
        == 1
    )

    assert report.healthy is False


def test_audit_reports_invalid_persisted_record(
    tmp_path: Path,
) -> None:
    database, repository = build_repository(
        tmp_path,
        "invalid-record-audit.db",
    )

    record = make_record(
        trace_id="trace-invalid-record",
        deployment_id="deployment-invalid-record",
    )

    repository.save(
        record
    )

    with database.transaction(
        immediate=True
    ) as connection:
        connection.execute(
            """
            UPDATE
                deployment_governance_traces
            SET
                payload = ?
            WHERE
                trace_id = ?
            """,
            (
                "{invalid-json",
                record.trace_id,
            ),
        )

    report = (
        DeploymentGovernanceIntegrityAuditService(
            repository
        )
        .audit()
    )

    assert report.total_records == 1

    assert (
        report.invalid_persisted_records
        == 1
    )

    assert report.healthy is False

    findings = (
        report.findings_for_status(
            GovernanceTraceIntegrityAuditStatus
            .INVALID_PERSISTED_RECORD
        )
    )

    assert len(
        findings
    ) == 1

    assert (
        findings[0].trace_id
        == record.trace_id
    )


def test_audit_collects_multiple_failure_types(
    tmp_path: Path,
) -> None:
    database, repository = build_repository(
        tmp_path,
        "multi-failure-audit.db",
    )

    records = (
        make_record(
            trace_id="trace-a-valid",
            deployment_id="deployment-a-valid",
        ),
        make_record(
            trace_id="trace-b-mismatch",
            deployment_id="deployment-b-mismatch",
        ),
        make_record(
            trace_id="trace-c-missing",
            deployment_id="deployment-c-missing",
        ),
        make_record(
            trace_id="trace-d-invalid-json",
            deployment_id="deployment-d-invalid-json",
        ),
    )

    repository.save_many(
        records
    )

    with database.transaction(
        immediate=True
    ) as connection:
        connection.execute(
            """
            UPDATE
                deployment_governance_traces
            SET
                environment = ?
            WHERE
                trace_id = ?
            """,
            (
                "staging",
                "trace-b-mismatch",
            ),
        )

        connection.execute(
            """
            UPDATE
                deployment_governance_traces
            SET
                integrity_algorithm = NULL,
                integrity_version = NULL,
                integrity_digest = NULL
            WHERE
                trace_id = ?
            """,
            (
                "trace-c-missing",
            ),
        )

        connection.execute(
            """
            UPDATE
                deployment_governance_traces
            SET
                payload = ?
            WHERE
                trace_id = ?
            """,
            (
                "{broken",
                "trace-d-invalid-json",
            ),
        )

    report = (
        DeploymentGovernanceIntegrityAuditService(
            repository
        )
        .audit(
            batch_size=2
        )
    )

    assert report.total_records == 4
    assert report.valid_records == 1
    assert report.invalid_records == 3

    assert report.integrity_mismatches == 1

    assert (
        report.missing_integrity_metadata
        == 1
    )

    assert (
        report.invalid_persisted_records
        == 1
    )

    assert report.healthy is False
