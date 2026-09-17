from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_search_cli import (
    _render_search_failure,
    _render_search_human,
    _render_search_json,
    run_deployment_governance_audit_search,
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


def test_render_search_human_no_matches() -> None:
    stdout = StringIO()

    _render_search_human((), stdout=stdout)

    output = stdout.getvalue()

    assert "Governance Audit Search" in output
    assert "No matching audits found." in output


def test_render_search_human_with_matches() -> None:
    records = (
        make_record(audit_id="audit-108", healthy=True),
        make_record(audit_id="audit-104", healthy=True),
    )

    stdout = StringIO()

    _render_search_human(records, stdout=stdout)

    output = stdout.getvalue()

    assert "Matches: 2" in output
    assert "audit-108" in output
    assert "audit-104" in output
    assert "HEALTHY" in output


def test_render_search_json() -> None:
    records = (make_record(audit_id="audit-1"),)

    stdout = StringIO()

    _render_search_json(records, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert isinstance(payload, list)
    assert payload[0]["audit_id"] == "audit-1"


def test_render_search_failure_human() -> None:
    stderr = StringIO()

    _render_search_failure(
        ValueError("at least one search filter must be specified"),
        json_output=False,
        stderr=stderr,
    )

    assert "could not be completed" in stderr.getvalue()


def test_render_search_failure_json() -> None:
    stderr = StringIO()

    _render_search_failure(
        ValueError("simulated"), json_output=True, stderr=stderr
    )

    payload = json.loads(stderr.getvalue())

    assert payload["status"] == "execution_failed"
    assert payload["exit_code"] == 2


def test_runner_requires_at_least_one_filter(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "search-runner-no-filter.db"),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_search(
        stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_runner_searches_by_healthy(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "search-runner-healthy.db"),
    )

    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
        deployment_governance_persistence_config_from_env,
    )

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )
    runtime.audit_history_repository.save(
        make_record(audit_id="A", healthy=True)
    )
    runtime.audit_history_repository.save(
        make_record(audit_id="B", offset_minutes=10, healthy=False)
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_search(
        healthy=True, json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert len(payload) == 1
    assert payload[0]["audit_id"] == "A"
