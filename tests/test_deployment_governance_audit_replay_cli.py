from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
)
from backend.observability.deployment_governance_audit_replay import (
    GovernanceIntegrityAuditReplay,
)
from backend.observability.deployment_governance_audit_replay_cli import (
    _render_replay_failure,
    _render_replay_human,
    _render_replay_json,
    _render_replay_list_human,
    _render_replay_list_json,
    run_deployment_governance_audit_replay,
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


def make_replay(*, audit_id: str, healthy: bool = True) -> GovernanceIntegrityAuditReplay:
    return GovernanceIntegrityAuditReplay(
        audit_id=audit_id,
        record=make_record(audit_id=audit_id, healthy=healthy),
        replayed_at=BASE_TIME,
    )


def test_render_replay_human_single() -> None:
    replay = make_replay(audit_id="audit-1", healthy=True)

    stdout = StringIO()

    _render_replay_human(replay, stdout=stdout)

    output = stdout.getvalue()

    assert "Governance Audit Replay" in output
    assert "Audit ID: audit-1" in output
    assert "Healthy: yes" in output
    assert "Started:" in output
    assert "Completed:" in output
    assert "Records Checked: 10" in output
    assert "Invalid Records: 0" in output
    assert "Integrity Mismatches: 0" in output


def test_render_replay_human_unhealthy() -> None:
    replay = make_replay(audit_id="audit-1", healthy=False)

    stdout = StringIO()

    _render_replay_human(replay, stdout=stdout)

    assert "Healthy: no" in stdout.getvalue()


def test_render_replay_list_human() -> None:
    replays = (
        make_replay(audit_id="audit-3"),
        make_replay(audit_id="audit-2"),
        make_replay(audit_id="audit-1"),
    )

    stdout = StringIO()

    _render_replay_list_human(replays, stdout=stdout)

    output = stdout.getvalue()

    assert "Replay History" in output
    assert "1. audit-3" in output
    assert "2. audit-2" in output
    assert "3. audit-1" in output


def test_render_replay_json_single() -> None:
    replay = make_replay(audit_id="audit-1")

    stdout = StringIO()

    _render_replay_json(replay, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert payload["audit_id"] == "audit-1"
    assert "replayed_at" in payload
    assert payload["record"]["audit_id"] == "audit-1"


def test_render_replay_list_json() -> None:
    replays = (
        make_replay(audit_id="audit-2"),
        make_replay(audit_id="audit-1"),
    )

    stdout = StringIO()

    _render_replay_list_json(replays, stdout=stdout)

    payload = json.loads(stdout.getvalue())

    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["audit_id"] == "audit-2"
    assert payload[1]["audit_id"] == "audit-1"


def test_render_replay_failure_human() -> None:
    stderr = StringIO()

    _render_replay_failure(
        KeyError("missing"), json_output=False, stderr=stderr
    )

    output = stderr.getvalue()

    assert "could not be completed" in output


def test_render_replay_failure_json() -> None:
    stderr = StringIO()

    _render_replay_failure(
        LookupError("empty history"), json_output=True, stderr=stderr
    )

    payload = json.loads(stderr.getvalue())

    assert payload["status"] == "execution_failed"
    assert payload["exit_code"] == 2


def test_runner_replays_latest_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "replay-runner-latest.db"),
    )

    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
        deployment_governance_persistence_config_from_env,
    )

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )
    runtime.audit_history_repository.save(make_record(audit_id="A"))
    runtime.audit_history_repository.save(make_record(audit_id="B"))

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_replay(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Audit ID: B" in stdout.getvalue()


def test_runner_replays_by_audit_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "replay-runner-by-id.db"),
    )

    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
        deployment_governance_persistence_config_from_env,
    )

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )
    runtime.audit_history_repository.save(make_record(audit_id="A"))
    runtime.audit_history_repository.save(make_record(audit_id="B"))

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_replay(
        audit_id="A", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Audit ID: A" in stdout.getvalue()


def test_runner_rejects_missing_audit_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "replay-runner-missing.db"),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_replay(
        audit_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_runner_handles_empty_history(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "replay-runner-empty.db"),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_replay(
        stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_runner_replays_recent_with_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / "replay-runner-limit.db"),
    )

    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
        deployment_governance_persistence_config_from_env,
    )

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )
    for index, audit_id in enumerate(["A", "B", "C", "D"]):
        runtime.audit_history_repository.save(
            make_record(audit_id=audit_id, offset_minutes=index)
        )

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_replay(
        limit=2, json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert [entry["audit_id"] for entry in payload] == ["D", "C"]
