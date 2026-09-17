from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_statistics import (
    calculate_governance_integrity_audit_statistics,
)
from backend.observability.deployment_governance_audit_statistics_cli import (
    _render_stats_failure,
    _render_stats_human,
    _render_stats_json,
    run_deployment_governance_audit_stats,
)


BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    healthy: bool = True,
) -> GovernanceIntegrityAuditRecord:
    invalid_records = 0 if healthy else 1

    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if healthy
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


def test_render_stats_human_empty_history() -> None:
    snapshot = calculate_governance_integrity_audit_statistics(())

    stdout = StringIO()

    _render_stats_human(snapshot, stdout=stdout)

    output = stdout.getvalue()

    assert "Governance Audit History Statistics" in output
    assert "No governance integrity audits have been recorded." in output


def test_render_stats_human_populated_history() -> None:
    records = (
        make_record(audit_id="audit-3", offset_minutes=30, healthy=True),
        make_record(audit_id="audit-2", offset_minutes=20, healthy=True),
        make_record(audit_id="audit-1", offset_minutes=10, healthy=False),
    )

    snapshot = calculate_governance_integrity_audit_statistics(records)

    stdout = StringIO()

    _render_stats_human(snapshot, stdout=stdout)

    output = stdout.getvalue()

    assert "Audits: 3" in output
    assert "Healthy: 2" in output
    assert "Unhealthy: 1" in output
    assert "Health rate:" in output
    assert "Current state: HEALTHY" in output
    assert "Current streak: 2 healthy audits" in output
    assert "Longest healthy streak:" in output
    assert "Longest unhealthy streak:" in output
    assert "First audit:" in output
    assert "Latest audit:" in output
    assert "Aggregate Audit Work" in output
    assert "Aggregate Failures" in output


def test_render_stats_human_singular_streak_label() -> None:
    records = (
        make_record(audit_id="audit-1", offset_minutes=0, healthy=True),
    )

    snapshot = calculate_governance_integrity_audit_statistics(records)

    stdout = StringIO()

    _render_stats_human(snapshot, stdout=stdout)

    output = stdout.getvalue()

    assert "Current streak: 1 healthy audit\n" in output
    assert "1 healthy audits" not in output


def test_render_stats_json() -> None:
    records = (
        make_record(audit_id="audit-1", offset_minutes=0, healthy=True),
    )

    snapshot = calculate_governance_integrity_audit_statistics(records)

    stdout = StringIO()

    _render_stats_json(snapshot, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert payload["total_audits"] == 1
    assert "health_rate" in payload
    assert "current_state" in payload
    assert "aggregate_failures" in payload


def test_render_stats_failure_human() -> None:
    stderr = StringIO()

    _render_stats_failure(
        RuntimeError("simulated failure"),
        json_output=False,
        stderr=stderr,
    )

    output = stderr.getvalue()

    assert "could not be calculated" in output
    assert "simulated failure" in output


def test_render_stats_failure_json() -> None:
    stderr = StringIO()

    _render_stats_failure(
        RuntimeError("simulated failure"),
        json_output=True,
        stderr=stderr,
    )

    payload = json.loads(stderr.getvalue())

    assert payload["status"] == "execution_failed"
    assert payload["error"] == "simulated failure"


def test_runner_handles_empty_history(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "stats-runner-empty.db"),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_stats(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "No governance integrity audits" in stdout.getvalue()


def test_runner_rejects_non_positive_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "stats-runner-bad-limit.db"),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_stats(
        limit=0, stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "limit must be greater than zero" in stderr.getvalue()
