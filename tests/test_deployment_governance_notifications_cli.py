from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_notifications_cli import (
    run_deployment_governance_notifications_clear,
    run_deployment_governance_notifications_delete,
    run_deployment_governance_notifications_list,
    run_deployment_governance_notifications_queue,
    run_deployment_governance_notifications_show,
)

# NOTE: the execution repository has no SQLite persistence
# (intentionally deferred, see deployment_governance_audit_worker.py),
# so each run_deployment_governance_notifications_queue call
# bootstraps its own fresh, empty in-memory execution history and
# therefore never generates alerts. The notification repository
# itself IS durable over SQLite, but with no alerts ever produced in
# these tests there is nothing to queue -- these tests exercise the
# empty/missing-id behavior of each command.


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_queue_with_no_alerts(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "notifications-queue-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notifications_queue(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Queued 0 notification(s)" in stdout.getvalue()


def test_queue_rejects_invalid_policy(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "notifications-queue-invalid.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_notifications_queue(
        minimum_success_rate=150.0,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "notifications-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notifications_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Notifications" in stdout.getvalue()
    assert "No governance audit notifications" in stdout.getvalue()


def test_list_json_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "notifications-list-json-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notifications_list(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "notifications-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_notifications_show(
        notification_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "notifications-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_notifications_delete(
        notification_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_clear_notifications(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "notifications-clear.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notifications_clear(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "cleared" in stdout.getvalue()


def test_clear_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "notifications-clear-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notifications_clear(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["status"] == "cleared"


def test_queue_list_show_delete_roundtrip_within_process(
    monkeypatch, tmp_path
) -> None:
    """
    Exercises the full queue -> list -> show -> delete lifecycle by
    driving the runtime directly in one process, since the execution
    repository (and therefore alert generation) does not survive
    across separate CLI bootstrap calls.
    """

    setup_env(monkeypatch, tmp_path, "notifications-roundtrip.db")

    from backend.observability.deployment_governance_audit_worker import (
        GovernanceIntegrityAuditExecutionRecord,
        GovernanceIntegrityExecutionResult,
    )
    from backend.observability.deployment_governance_execution_alerts import (
        GovernanceIntegrityAlertPolicy,
    )
    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
        deployment_governance_persistence_config_from_env,
    )
    from datetime import datetime, timedelta, timezone

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    started_at = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

    runtime.execution_repository.save(
        GovernanceIntegrityAuditExecutionRecord(
            job_id="job-1",
            schedule_name="nightly",
            template_name="nightly",
            result=GovernanceIntegrityExecutionResult.FAILED,
            report=None,
            error="boom",
            started_at=started_at,
            finished_at=started_at + timedelta(milliseconds=100),
        )
    )

    policy = GovernanceIntegrityAlertPolicy(
        minimum_success_rate=90.0,
        maximum_failure_rate=0.0,
        maximum_average_duration_ms=1_000_000.0,
    )

    notifications = (
        runtime.build_integrity_notification_service().queue(policy)
    )

    assert len(notifications) >= 1

    notification_id = notifications[0].notification_id

    list_stdout = StringIO()

    run_deployment_governance_notifications_list(
        stdout=list_stdout, stderr=StringIO()
    )

    assert notification_id in list_stdout.getvalue()

    show_stdout = StringIO()

    show_exit_code = run_deployment_governance_notifications_show(
        notification_id=notification_id,
        stdout=show_stdout,
        stderr=StringIO(),
    )

    assert show_exit_code == 0
    assert notification_id in show_stdout.getvalue()

    delete_stdout = StringIO()

    delete_exit_code = run_deployment_governance_notifications_delete(
        notification_id=notification_id,
        stdout=delete_stdout,
        stderr=StringIO(),
    )

    assert delete_exit_code == 0

    final_show_stderr = StringIO()

    final_show_exit_code = run_deployment_governance_notifications_show(
        notification_id=notification_id,
        stdout=StringIO(),
        stderr=final_show_stderr,
    )

    assert final_show_exit_code == 2
