from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_delivery_engine_cli import (
    run_deployment_governance_delivery_run,
    run_deployment_governance_delivery_run_all,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_run_missing_dispatch(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "deliver-run-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_delivery_run(
        dispatch_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_run_all_with_nothing_queued(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "deliver-run-all-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_run_all(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Delivered 0 dispatch(es)" in stdout.getvalue()


def test_run_all_json_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "deliver-run-all-empty-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_run_all(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_run_and_run_all_within_process(monkeypatch, tmp_path) -> None:
    """
    Exercises delivering a real dispatch by driving the runtime
    directly in one process, since notifications and dispatches are
    created from a separate, non-durable execution history that does
    not survive across separate CLI bootstrap calls.
    """

    setup_env(monkeypatch, tmp_path, "deliver-roundtrip.db")

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

    run_stdout = StringIO()

    run_exit_code = run_deployment_governance_delivery_run(
        dispatch_id=dispatch_id, stdout=run_stdout, stderr=StringIO()
    )

    assert run_exit_code == 0
    assert "Status: success" in run_stdout.getvalue()

    run_all_stdout = StringIO()

    run_all_exit_code = run_deployment_governance_delivery_run_all(
        stdout=run_all_stdout, stderr=StringIO()
    )

    assert run_all_exit_code == 0
    assert "Delivered 1 dispatch(es)" in run_all_stdout.getvalue()
