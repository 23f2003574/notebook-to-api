from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_delivery_history_cli import (
    run_deployment_governance_delivery_history_clear,
    run_deployment_governance_delivery_history_list,
    run_deployment_governance_delivery_history_show,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-history-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_history_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Delivery History" in stdout.getvalue()
    assert "No governance audit delivery history records" in stdout.getvalue()


def test_list_json_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-history-list-json-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_history_list(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-history-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_delivery_history_show(
        delivery_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_clear(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-history-clear.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_history_clear(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "cleared" in stdout.getvalue()


def test_clear_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-history-clear-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_history_clear(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["status"] == "cleared"


def test_list_and_show_after_recording_within_process(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-history-roundtrip.db")

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

    runtime.build_integrity_delivery_history_service().deliver(
        dispatches[0].dispatch_id
    )

    list_stdout = StringIO()

    run_deployment_governance_delivery_history_list(
        stdout=list_stdout, stderr=StringIO()
    )

    assert dispatches[0].dispatch_id in list_stdout.getvalue()

    show_stdout = StringIO()

    show_exit_code = run_deployment_governance_delivery_history_show(
        delivery_id=dispatches[0].dispatch_id,
        stdout=show_stdout,
        stderr=StringIO(),
    )

    assert show_exit_code == 0
    assert "Status: success" in show_stdout.getvalue()
