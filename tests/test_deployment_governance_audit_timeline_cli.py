from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_timeline import (
    GovernanceIntegrityAuditTimelineEvent,
    GovernanceIntegrityAuditTimelineState,
)
from backend.observability.deployment_governance_audit_timeline_cli import (
    _render_timeline_failure,
    _render_timeline_human,
    _render_timeline_json,
    run_deployment_governance_audit_timeline,
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


def make_event(
    *, audit_id: str, healthy: bool = True
) -> GovernanceIntegrityAuditTimelineEvent:
    record = make_record(audit_id=audit_id, healthy=healthy)

    return GovernanceIntegrityAuditTimelineEvent(
        audit_id=record.audit_id,
        started_at=record.started_at,
        completed_at=record.completed_at,
        state=(
            GovernanceIntegrityAuditTimelineState.HEALTHY
            if healthy
            else GovernanceIntegrityAuditTimelineState.UNHEALTHY
        ),
        total_records=record.total_records,
        invalid_records=record.invalid_records,
        integrity_mismatches=record.integrity_mismatches,
    )


def test_render_timeline_human_empty() -> None:
    stdout = StringIO()

    _render_timeline_human((), stdout=stdout)

    output = stdout.getvalue()

    assert "Governance Audit Timeline" in output
    assert "No governance integrity audits have been recorded." in output


def test_render_timeline_human_populated() -> None:
    events = (
        make_event(audit_id="audit-103", healthy=True),
        make_event(audit_id="audit-102", healthy=False),
        make_event(audit_id="audit-101", healthy=True),
    )

    stdout = StringIO()

    _render_timeline_human(events, stdout=stdout)

    output = stdout.getvalue()

    assert "Governance Audit Timeline" in output
    assert "audit-103" in output
    assert "HEALTHY" in output
    assert "audit-102" in output
    assert "UNHEALTHY" in output
    assert "audit-101" in output


def test_render_timeline_json() -> None:
    events = (make_event(audit_id="audit-1", healthy=True),)

    stdout = StringIO()

    _render_timeline_json(events, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert isinstance(payload, list)
    assert payload[0]["audit_id"] == "audit-1"
    assert payload[0]["state"] == "healthy"


def test_render_timeline_failure_human() -> None:
    stderr = StringIO()

    _render_timeline_failure(
        RuntimeError("simulated failure"),
        json_output=False,
        stderr=stderr,
    )

    output = stderr.getvalue()

    assert "could not be produced" in output


def test_render_timeline_failure_json() -> None:
    stderr = StringIO()

    _render_timeline_failure(
        RuntimeError("simulated failure"),
        json_output=True,
        stderr=stderr,
    )

    payload = json.loads(stderr.getvalue())

    assert payload["status"] == "execution_failed"
    assert payload["exit_code"] == 2


def test_runner_handles_empty_history(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "timeline-runner-empty.db"),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_timeline(
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
        str(tmp_path / "timeline-runner-bad-limit.db"),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_timeline(
        limit=0, stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be produced" in stderr.getvalue()


def test_runner_respects_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "timeline-runner-limit.db"),
    )

    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
        deployment_governance_persistence_config_from_env,
    )

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )
    for index in range(5):
        runtime.audit_history_repository.save(
            make_record(audit_id=f"audit-{index}", offset_minutes=index)
        )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_timeline(
        limit=3, json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert len(payload) == 3
