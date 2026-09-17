from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
)
from backend.observability.deployment_governance_audit_regression import (
    GovernanceIntegrityRegressionSnapshot,
    GovernanceIntegrityRegressionStatus,
)
from backend.observability.deployment_governance_check import (
    GovernanceIntegrityCheckPolicy,
    GovernanceIntegrityCheckResult,
    GovernanceIntegrityCheckStatus,
)
from backend.observability.deployment_governance_check_cli import (
    GovernanceIntegrityCheckExitCode,
    _render_check_failure,
    _render_check_human,
    _render_check_json,
    run_deployment_governance_check,
)


def make_check_result(
    *,
    passed: bool,
    regression_detected: bool = False,
    audit_healthy: bool | None = None,
    retention=None,
) -> GovernanceIntegrityCheckResult:
    if regression_detected:
        status = GovernanceIntegrityCheckStatus.REGRESSION_DETECTED
    elif not passed:
        status = GovernanceIntegrityCheckStatus.UNHEALTHY
    else:
        status = GovernanceIntegrityCheckStatus.PASSED

    resolved_audit_healthy = (
        passed if audit_healthy is None else audit_healthy
    )

    regression = GovernanceIntegrityRegressionSnapshot(
        status=(
            GovernanceIntegrityRegressionStatus.REGRESSION
            if regression_detected
            else GovernanceIntegrityRegressionStatus.HEALTHY
        ),
        regression_detected=regression_detected,
        current_audit_id="audit-current",
        baseline_audit_id="audit-baseline",
        current_outcome=(
            GovernanceIntegrityAuditOutcome.UNHEALTHY
            if regression_detected
            else GovernanceIntegrityAuditOutcome.HEALTHY
        ),
        baseline_outcome=GovernanceIntegrityAuditOutcome.HEALTHY,
        current_invalid_records=1 if regression_detected else 0,
        baseline_invalid_records=0,
        invalid_record_delta=1 if regression_detected else 0,
        integrity_mismatch_delta=1 if regression_detected else 0,
        missing_integrity_metadata_delta=0,
        invalid_integrity_metadata_delta=0,
        invalid_persisted_records_delta=0,
        newly_introduced_failure_categories=(
            ("integrity_mismatches",) if regression_detected else ()
        ),
    )

    return GovernanceIntegrityCheckResult(
        status=status,
        policy=GovernanceIntegrityCheckPolicy.REGRESSION_ONLY,
        passed=passed,
        audit_id="audit-current",
        audit_healthy=resolved_audit_healthy,
        regression=regression,
        retention=retention,
    )


def test_check_human_output_renders_passed_result() -> None:
    result = make_check_result(passed=True)

    stdout = StringIO()

    _render_check_human(result, stdout=stdout)

    output = stdout.getvalue()

    assert "Deployment Governance Integrity Check" in output
    assert "Status: PASSED" in output
    assert "Passed: yes" in output


def test_check_human_output_renders_regression_failure() -> None:
    result = make_check_result(passed=False, regression_detected=True)

    stdout = StringIO()

    _render_check_human(result, stdout=stdout)

    output = stdout.getvalue()

    assert "Status: REGRESSION_DETECTED" in output
    assert "Regression detected: yes" in output
    assert "New failure categories:" in output
    assert "  integrity_mismatches" in output


def test_check_human_output_renders_unhealthy_failure() -> None:
    result = make_check_result(passed=False, audit_healthy=False)

    stdout = StringIO()

    _render_check_human(result, stdout=stdout)

    output = stdout.getvalue()

    assert "Status: UNHEALTHY" in output
    assert "Audit healthy: no" in output
    assert "Passed: no" in output


def test_check_human_output_omits_retention_section_by_default() -> None:
    result = make_check_result(passed=True)

    stdout = StringIO()

    _render_check_human(result, stdout=stdout)

    assert "Automatic Retention" not in stdout.getvalue()


def test_check_human_output_renders_retention_section_when_present() -> None:
    from backend.observability.deployment_governance_audit_retention import (
        GovernanceIntegrityAuditPruningPlan,
        GovernanceIntegrityAuditPruningResult,
    )

    plan = GovernanceIntegrityAuditPruningPlan(
        evaluated_at=datetime.now(timezone.utc),
        total_records=101,
        retained_records=100,
        prunable_records=1,
        retained_audit_ids=tuple(f"audit-{i}" for i in range(100)),
        prunable_audit_ids=("audit-oldest",),
        oldest_retained_started_at=None,
        newest_retained_started_at=None,
    )

    retention = GovernanceIntegrityAuditPruningResult(
        plan=plan, applied=True, deleted_records=1
    )

    result = make_check_result(passed=True, retention=retention)

    stdout = StringIO()

    _render_check_human(result, stdout=stdout)

    output = stdout.getvalue()

    assert "Automatic Retention" in output
    assert "Applied: yes" in output
    assert "Prunable records: 1" in output
    assert "Deleted records: 1" in output
    assert "Records retained: 100" in output


def test_check_json_output_is_valid_json() -> None:
    result = make_check_result(passed=True)

    stdout = StringIO()

    _render_check_json(result, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert payload["passed"] is True
    assert "regression" in payload
    assert payload["audit_id"] == "audit-current"


def test_render_check_failure_human() -> None:
    stderr = StringIO()

    _render_check_failure(
        RuntimeError("simulated failure"),
        json_output=False,
        stderr=stderr,
    )

    output = stderr.getvalue()

    assert "could not be executed" in output
    assert "simulated failure" in output


def test_render_check_failure_json() -> None:
    stderr = StringIO()

    _render_check_failure(
        RuntimeError("simulated failure"),
        json_output=True,
        stderr=stderr,
    )

    payload = json.loads(stderr.getvalue())

    assert payload["status"] == "execution_failed"
    assert payload["passed"] is False
    assert payload["error"] == "simulated failure"
    assert payload["exit_code"] == int(
        GovernanceIntegrityCheckExitCode.EXECUTION_FAILED
    )


def test_check_runner_returns_zero_when_policy_passes(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "check-runner-pass.db"),
    )

    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_deployment_governance_check(
        stdout=stdout, stderr=stderr
    )

    assert exit_code == 0
    assert "Status: PASSED" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_check_runner_returns_three_when_policy_fails(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "check-runner-fail.db"

    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(database_path)
    )

    first_exit_code = run_deployment_governance_check(
        stdout=StringIO(), stderr=StringIO()
    )

    assert first_exit_code == 0

    connection = sqlite3.connect(str(database_path))
    connection.execute(
        """
        INSERT INTO deployment_governance_traces (
            trace_id, deployment_id, service_name, environment,
            artifact_digest, created_at, updated_at, governance_state,
            final_status, completed, payload
        ) VALUES (
            'trace-check-regression', 'deployment-check-regression',
            'payments-api', 'staging', 'sha256:regression',
            '2026-07-15T00:00:00+00:00', '2026-07-15T00:00:00+00:00',
            'created', NULL, 0, '{}'
        )
        """
    )
    connection.commit()
    connection.close()

    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_deployment_governance_check(
        stdout=stdout, stderr=stderr
    )

    assert exit_code == 3
    assert "Status: REGRESSION_DETECTED" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_check_runner_returns_two_when_execution_fails(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "memory"
    )

    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_deployment_governance_check(
        stdout=stdout, stderr=stderr
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "could not be executed" in stderr.getvalue()
