from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_notification_dispatcher_cli import (
    run_deployment_governance_notification_dispatch_clear,
    run_deployment_governance_notification_dispatch_delete,
    run_deployment_governance_notification_dispatch_list,
    run_deployment_governance_notification_dispatch_run,
    run_deployment_governance_notification_dispatch_show,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_run_with_nothing_pending(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dispatch-run-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_dispatch_run(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Dispatched 0 record(s)" in stdout.getvalue()


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dispatch-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_dispatch_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Notification Dispatches" in stdout.getvalue()
    assert (
        "No governance audit notification dispatches" in stdout.getvalue()
    )


def test_list_json_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dispatch-list-json-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_dispatch_list(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dispatch-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_notification_dispatch_show(
        dispatch_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dispatch-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_notification_dispatch_delete(
        dispatch_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_clear(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dispatch-clear.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_dispatch_clear(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "cleared" in stdout.getvalue()


def test_clear_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dispatch-clear-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_dispatch_clear(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["status"] == "cleared"


def test_run_list_show_delete_roundtrip_within_process(
    monkeypatch, tmp_path
) -> None:
    """
    Exercises the full run -> list -> show -> delete lifecycle by
    driving the runtime directly in one process, since notifications
    are created via a separate, non-durable execution history that
    does not survive across separate CLI bootstrap calls.
    """

    setup_env(monkeypatch, tmp_path, "dispatch-roundtrip.db")

    from datetime import datetime, timezone

    from backend.observability.deployment_governance_execution_alerts import (
        GovernanceIntegrityAlertSeverity,
    )
    from backend.observability.deployment_governance_notification_channels import (
        GovernanceIntegrityNotificationChannelType,
    )
    from backend.observability.deployment_governance_notifications import (
        GovernanceIntegrityNotification,
        GovernanceIntegrityNotificationStatus,
    )
    from backend.observability.deployment_governance_persistence import (
        build_deployment_governance_persistence,
        deployment_governance_persistence_config_from_env,
    )

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.notification_repository.save(
        GovernanceIntegrityNotification(
            notification_id="n1",
            alert_id="alert-1",
            severity=GovernanceIntegrityAlertSeverity.WARNING,
            message="boom",
            status=GovernanceIntegrityNotificationStatus.PENDING,
            created_at=datetime(
                2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc
            ),
        )
    )

    runtime.build_integrity_notification_channel_service().create(
        "email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    dispatches = (
        runtime.build_integrity_notification_dispatcher()
        .dispatch_pending()
    )

    assert len(dispatches) == 1

    dispatch_id = dispatches[0].dispatch_id

    list_stdout = StringIO()

    run_deployment_governance_notification_dispatch_list(
        stdout=list_stdout, stderr=StringIO()
    )

    assert dispatch_id in list_stdout.getvalue()

    show_stdout = StringIO()

    show_exit_code = run_deployment_governance_notification_dispatch_show(
        dispatch_id=dispatch_id, stdout=show_stdout, stderr=StringIO()
    )

    assert show_exit_code == 0
    assert dispatch_id in show_stdout.getvalue()

    delete_stdout = StringIO()

    delete_exit_code = (
        run_deployment_governance_notification_dispatch_delete(
            dispatch_id=dispatch_id,
            stdout=delete_stdout,
            stderr=StringIO(),
        )
    )

    assert delete_exit_code == 0

    final_show_stderr = StringIO()

    final_show_exit_code = (
        run_deployment_governance_notification_dispatch_show(
            dispatch_id=dispatch_id,
            stdout=StringIO(),
            stderr=final_show_stderr,
        )
    )

    assert final_show_exit_code == 2
