from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_session import (
    GovernanceIntegrityAuditSession,
)
from backend.observability.deployment_governance_audit_session_cli import (
    _render_session_failure,
    _render_session_human,
    _render_session_json,
    run_deployment_governance_audit_session,
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


def make_session(
    audit_ids: tuple[str, ...],
) -> GovernanceIntegrityAuditSession:
    records = tuple(
        make_record(audit_id=audit_id, offset_minutes=index)
        for index, audit_id in enumerate(reversed(audit_ids))
    )[::-1]

    return GovernanceIntegrityAuditSession(
        records=records,
        total_audits=len(records),
        first_audit_id=records[-1].audit_id if records else None,
        latest_audit_id=records[0].audit_id if records else None,
    )


def test_render_session_human_empty() -> None:
    stdout = StringIO()

    _render_session_human(make_session(()), stdout=stdout)

    output = stdout.getvalue()

    assert "Governance Audit Session" in output
    assert "No governance integrity audits have been recorded." in output


def test_render_session_human_populated() -> None:
    session = make_session(
        ("audit-105", "audit-104", "audit-103", "audit-102", "audit-101")
    )

    stdout = StringIO()

    _render_session_human(session, stdout=stdout)

    output = stdout.getvalue()

    assert "Audits: 5" in output
    assert "Latest : audit-105" in output
    assert "Oldest : audit-101" in output
    assert "History" in output
    assert "1. audit-105" in output
    assert "5. audit-101" in output


def test_render_session_json() -> None:
    session = make_session(("audit-1",))

    stdout = StringIO()

    _render_session_json(session, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert payload["total_audits"] == 1
    assert payload["latest_audit_id"] == "audit-1"
    assert payload["first_audit_id"] == "audit-1"
    assert len(payload["records"]) == 1


def test_render_session_failure_human() -> None:
    stderr = StringIO()

    _render_session_failure(
        RuntimeError("simulated failure"),
        json_output=False,
        stderr=stderr,
    )

    assert "could not be reconstructed" in stderr.getvalue()


def test_render_session_failure_json() -> None:
    stderr = StringIO()

    _render_session_failure(
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
        str(tmp_path / "session-runner-empty.db"),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_session(
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
        str(tmp_path / "session-runner-bad-limit.db"),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_session(
        limit=0, stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_runner_respects_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "session-runner-limit.db"),
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

    exit_code = run_deployment_governance_audit_session(
        limit=3, json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["total_audits"] == 3
