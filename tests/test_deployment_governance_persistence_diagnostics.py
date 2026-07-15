from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceBackend,
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.deployment_governance_trace_repository import (
    GovernanceTraceRecord,
)


def make_record(
    *,
    trace_id: str,
) -> GovernanceTraceRecord:
    timestamp = datetime(
        2026,
        7,
        14,
        15,
        0,
        0,
        tzinfo=timezone.utc,
    )

    return GovernanceTraceRecord(
        trace_id=trace_id,
        deployment_id=(
            f"deployment-{trace_id}"
        ),
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
            },
            "events": [],
        },
    )


def test_memory_diagnostics_snapshot() -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.memory()
        )
    )

    runtime.repository.save(
        make_record(
            trace_id="trace-memory-diagnostics"
        )
    )

    snapshot = (
        runtime
        .build_diagnostics_service()
        .capture()
    )

    assert (
        snapshot.backend
        is DeploymentGovernancePersistenceBackend.MEMORY
    )

    assert snapshot.durable is False

    assert snapshot.database_path is None

    assert snapshot.schema is None

    assert (
        snapshot.repository.total_records
        == 1
    )

    assert (
        snapshot.integrity.supported
        is False
    )

    assert (
        snapshot.integrity.executed
        is False
    )

    assert (
        snapshot.integrity.healthy
        is None
    )

    assert (
        snapshot.operationally_healthy
        is True
    )

    assert (
        snapshot.integrity_verified
        is False
    )


def test_sqlite_lightweight_diagnostics_snapshot(
    tmp_path: Path,
) -> None:
    database_path = (
        tmp_path
        / "diagnostics.db"
    )

    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                database_path
            )
        )
    )

    runtime.repository.save_many(
        (
            make_record(
                trace_id="trace-sqlite-001"
            ),
            make_record(
                trace_id="trace-sqlite-002"
            ),
        )
    )

    snapshot = (
        runtime
        .build_diagnostics_service()
        .capture(
            include_integrity_audit=False
        )
    )

    assert (
        snapshot.backend
        is DeploymentGovernancePersistenceBackend.SQLITE
    )

    assert snapshot.durable is True

    assert (
        snapshot.database_path
        == database_path
    )

    assert snapshot.schema is not None

    assert (
        snapshot.schema.current_version
        == 4
    )

    assert (
        snapshot.schema.applied_versions
        == (
            1,
            2,
            3,
            4,
        )
    )

    assert (
        snapshot.schema.migration_count
        == 4
    )

    assert (
        snapshot.repository.total_records
        == 2
    )

    assert (
        snapshot.repository.statistics["total_traces"]
        == 2
    )

    assert (
        snapshot.integrity.supported
        is True
    )

    assert (
        snapshot.integrity.executed
        is False
    )

    assert (
        snapshot.integrity.healthy
        is None
    )

    assert (
        snapshot.operationally_healthy
        is True
    )

    assert (
        snapshot.integrity_verified
        is False
    )


def test_sqlite_deep_diagnostics_executes_integrity_audit(
    tmp_path: Path,
) -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                tmp_path
                / "deep-diagnostics.db"
            )
        )
    )

    runtime.repository.save_many(
        (
            make_record(
                trace_id="trace-deep-001"
            ),
            make_record(
                trace_id="trace-deep-002"
            ),
            make_record(
                trace_id="trace-deep-003"
            ),
        )
    )

    snapshot = (
        runtime
        .build_diagnostics_service()
        .capture(
            include_integrity_audit=True
        )
    )

    assert (
        snapshot.integrity.supported
        is True
    )

    assert (
        snapshot.integrity.executed
        is True
    )

    assert (
        snapshot.integrity.healthy
        is True
    )

    assert (
        snapshot.integrity.total_records
        == 3
    )

    assert (
        snapshot.integrity.valid_records
        == 3
    )

    assert (
        snapshot.integrity.invalid_records
        == 0
    )

    assert (
        snapshot.operationally_healthy
        is True
    )

    assert (
        snapshot.integrity_verified
        is True
    )


def test_sqlite_deep_diagnostics_reports_corruption(
    tmp_path: Path,
) -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                tmp_path
                / "corrupted-diagnostics.db"
            )
        )
    )

    record = make_record(
        trace_id="trace-corrupted-diagnostics"
    )

    runtime.repository.save(
        record
    )

    assert runtime.database is not None

    with runtime.database.transaction(
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
                record.trace_id,
            ),
        )

    snapshot = (
        runtime
        .build_diagnostics_service()
        .capture(
            include_integrity_audit=True
        )
    )

    assert (
        snapshot.integrity.executed
        is True
    )

    assert (
        snapshot.integrity.healthy
        is False
    )

    assert (
        snapshot.integrity.invalid_records
        == 1
    )

    assert (
        snapshot.integrity.integrity_mismatches
        == 1
    )

    assert (
        snapshot.operationally_healthy
        is False
    )

    assert (
        snapshot.integrity_verified
        is False
    )


def test_diagnostics_snapshot_serializes_to_json_compatible_dict(
    tmp_path: Path,
) -> None:
    database_path = (
        tmp_path
        / "serialized-diagnostics.db"
    )

    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                database_path
            )
        )
    )

    snapshot = (
        runtime
        .build_diagnostics_service()
        .capture(
            include_integrity_audit=True
        )
    )

    payload = snapshot.to_dict()

    assert (
        payload["backend"]
        == "sqlite"
    )

    assert (
        payload["durable"]
        is True
    )

    assert (
        payload["database_path"]
        == str(
            database_path
        )
    )

    assert (
        payload["schema"]["current_version"]
        == 4
    )

    assert (
        payload["schema"]["applied_versions"]
        == [
            1,
            2,
            3,
            4,
        ]
    )

    assert (
        payload["integrity"]["supported"]
        is True
    )

    assert (
        payload["integrity"]["executed"]
        is True
    )

    assert (
        payload["operationally_healthy"]
        is True
    )

    assert (
        payload["integrity_verified"]
        is True
    )

    serialized = json.dumps(
        payload
    )

    assert isinstance(
        serialized,
        str,
    )


def test_diagnostics_rejects_invalid_integrity_audit_batch_size() -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.memory()
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            "integrity_audit_batch_size "
            "must be greater than zero"
        ),
    ):
        (
            runtime
            .build_diagnostics_service()
            .capture(
                integrity_audit_batch_size=0
            )
        )
