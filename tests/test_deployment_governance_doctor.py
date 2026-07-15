from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from backend.observability.deployment_governance_doctor import (
    DeploymentGovernanceDoctor,
    GovernanceDoctorExitCode,
    GovernanceDoctorOptions,
)
from backend.observability.deployment_governance_persistence import (
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
        16,
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


def test_doctor_reports_healthy_memory_runtime() -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.memory()
        )
    )

    doctor = DeploymentGovernanceDoctor(
        runtime
    )

    result = doctor.run(
        GovernanceDoctorOptions()
    )

    assert (
        result.exit_code
        is GovernanceDoctorExitCode.HEALTHY
    )

    assert result.snapshot is not None

    assert (
        result.snapshot.operationally_healthy
        is True
    )

    assert result.error is None


def test_doctor_renders_human_readable_output() -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.memory()
        )
    )

    doctor = DeploymentGovernanceDoctor(
        runtime
    )

    stdout = StringIO()

    stderr = StringIO()

    exit_code = doctor.execute(
        GovernanceDoctorOptions(),
        stdout=stdout,
        stderr=stderr,
    )

    output = stdout.getvalue()

    assert (
        exit_code
        is GovernanceDoctorExitCode.HEALTHY
    )

    assert (
        "Deployment Governance Persistence Doctor"
        in output
    )

    assert (
        "Status: HEALTHY"
        in output
    )

    assert (
        "Backend: memory"
        in output
    )

    assert (
        "Durable: no"
        in output
    )

    assert (
        "Audit History"
        in output
    )

    assert (
        "Recorded audits: 0"
        in output
    )

    assert (
        stderr.getvalue()
        == ""
    )


def test_doctor_renders_json_output() -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.memory()
        )
    )

    doctor = DeploymentGovernanceDoctor(
        runtime
    )

    stdout = StringIO()

    stderr = StringIO()

    exit_code = doctor.execute(
        GovernanceDoctorOptions(
            json_output=True
        ),
        stdout=stdout,
        stderr=stderr,
    )

    payload = json.loads(
        stdout.getvalue()
    )

    assert (
        exit_code
        is GovernanceDoctorExitCode.HEALTHY
    )

    assert (
        payload["backend"]
        == "memory"
    )

    assert (
        payload["operationally_healthy"]
        is True
    )

    assert (
        stderr.getvalue()
        == ""
    )


def test_doctor_runs_deep_sqlite_integrity_audit(
    tmp_path: Path,
) -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                tmp_path
                / "doctor-deep.db"
            )
        )
    )

    doctor = DeploymentGovernanceDoctor(
        runtime
    )

    result = doctor.run(
        GovernanceDoctorOptions(
            deep=True
        )
    )

    assert (
        result.exit_code
        is GovernanceDoctorExitCode.HEALTHY
    )

    assert result.snapshot is not None

    assert (
        result.snapshot.integrity.executed
        is True
    )

    assert (
        result.snapshot.integrity.healthy
        is True
    )

    assert (
        result.snapshot.integrity_verified
        is True
    )

    assert (
        result.snapshot.audit_history.current_audit_recorded
        is True
    )

    assert (
        result.snapshot.audit_history.total_audits
        == 1
    )


def test_doctor_renders_audit_history_in_deep_human_output(
    tmp_path: Path,
) -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                tmp_path
                / "doctor-deep-human.db"
            )
        )
    )

    doctor = DeploymentGovernanceDoctor(
        runtime
    )

    stdout = StringIO()

    stderr = StringIO()

    doctor.execute(
        GovernanceDoctorOptions(
            deep=True
        ),
        stdout=stdout,
        stderr=stderr,
    )

    output = stdout.getvalue()

    assert (
        "Current audit recorded: yes"
        in output
    )

    assert (
        "Current audit ID:"
        in output
    )

    assert (
        "Latest audit status: HEALTHY"
        in output
    )


def test_doctor_returns_unhealthy_exit_code_for_corrupted_store(
    tmp_path: Path,
) -> None:
    runtime = (
        build_deployment_governance_persistence(
            DeploymentGovernancePersistenceConfig.sqlite(
                tmp_path
                / "doctor-corrupted.db"
            )
        )
    )

    record = make_record(
        trace_id="trace-doctor-corrupted"
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

    doctor = DeploymentGovernanceDoctor(
        runtime
    )

    result = doctor.run(
        GovernanceDoctorOptions(
            deep=True
        )
    )

    assert (
        result.exit_code
        is GovernanceDoctorExitCode.UNHEALTHY
    )

    assert result.snapshot is not None

    assert (
        result.snapshot.operationally_healthy
        is False
    )

    assert (
        result.snapshot.integrity.integrity_mismatches
        == 1
    )


class FailingDiagnosticsService:
    def capture(
        self,
        **_: object,
    ) -> object:
        raise RuntimeError(
            "simulated diagnostics failure"
        )


class FailingRuntime:
    def build_diagnostics_service(
        self,
    ) -> FailingDiagnosticsService:
        return FailingDiagnosticsService()


def test_doctor_converts_diagnostics_failure_to_exit_code_two() -> None:
    doctor = DeploymentGovernanceDoctor(
        FailingRuntime()
    )

    result = doctor.run(
        GovernanceDoctorOptions()
    )

    assert (
        result.exit_code
        is GovernanceDoctorExitCode.DIAGNOSTICS_FAILED
    )

    assert result.snapshot is None

    assert (
        result.error
        == "simulated diagnostics failure"
    )


def test_doctor_execute_renders_failure_and_returns_exit_code_two() -> None:
    doctor = DeploymentGovernanceDoctor(
        FailingRuntime()
    )

    stdout = StringIO()

    stderr = StringIO()

    exit_code = doctor.execute(
        GovernanceDoctorOptions(),
        stdout=stdout,
        stderr=stderr,
    )

    assert (
        exit_code
        is GovernanceDoctorExitCode.DIAGNOSTICS_FAILED
    )

    assert stdout.getvalue() == ""

    assert (
        "simulated diagnostics failure"
        in stderr.getvalue()
    )


def test_doctor_options_reject_invalid_batch_size() -> None:
    import pytest

    with pytest.raises(
        ValueError,
        match="integrity_audit_batch_size",
    ):
        GovernanceDoctorOptions(
            integrity_audit_batch_size=0
        )
