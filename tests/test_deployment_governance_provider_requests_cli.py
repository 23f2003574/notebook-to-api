from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO

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
from backend.observability.deployment_governance_provider_requests_cli import (
    run_deployment_governance_provider_request_show,
    run_deployment_governance_provider_request_validate,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def _seed_webhook_notification(monkeypatch, tmp_path, name: str) -> str:
    setup_env(monkeypatch, tmp_path, name)

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    notification_id = "n1"

    runtime.notification_repository.save(
        GovernanceIntegrityNotification(
            notification_id=notification_id,
            alert_id="alert-1",
            severity=GovernanceIntegrityAlertSeverity.WARNING,
            message="boom",
            status=GovernanceIntegrityNotificationStatus.PENDING,
            created_at=BASE_TIME,
        )
    )

    runtime.build_integrity_notification_channel_service().create(
        "webhook",
        GovernanceIntegrityNotificationChannelType.WEBHOOK,
        "https://example.com/hook",
    )

    runtime.build_integrity_provider_secrets_service().create(
        GovernanceIntegrityNotificationChannelType.WEBHOOK,
        {"api_key": "abc123"},
    )

    return notification_id


# --- show ------------------------------------------------------------------


def test_show_redacts_header_values(monkeypatch, tmp_path) -> None:
    notification_id = _seed_webhook_notification(
        monkeypatch, tmp_path, "request-show.db"
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_request_show(
        channel_type="webhook",
        notification_id=notification_id,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Method: POST" in output
    assert "abc123" not in output


def test_show_json_redacts_header_values(monkeypatch, tmp_path) -> None:
    notification_id = _seed_webhook_notification(
        monkeypatch, tmp_path, "request-show-json.db"
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_request_show(
        channel_type="webhook",
        notification_id=notification_id,
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["method"] == "POST"
    assert payload["headers"]["X-API-Key"] == "***REDACTED***"
    assert "abc123" not in stdout.getvalue()


def test_show_fails_for_missing_notification(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "request-show-missing.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )
    runtime.build_integrity_notification_channel_service().create(
        "webhook",
        GovernanceIntegrityNotificationChannelType.WEBHOOK,
        "https://example.com/hook",
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_request_show(
        channel_type="webhook",
        notification_id="does-not-exist",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_show_fails_for_missing_secret(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "request-show-missing-secret.db")

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
            created_at=BASE_TIME,
        )
    )
    runtime.build_integrity_notification_channel_service().create(
        "webhook",
        GovernanceIntegrityNotificationChannelType.WEBHOOK,
        "https://example.com/hook",
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_request_show(
        channel_type="webhook",
        notification_id="n1",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "abc123" not in stderr.getvalue()


# --- validate ------------------------------------------------------------


def test_validate_succeeds_with_seeded_secret(monkeypatch, tmp_path) -> None:
    notification_id = _seed_webhook_notification(
        monkeypatch, tmp_path, "request-validate.db"
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_request_validate(
        channel_type="webhook",
        notification_id=notification_id,
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["valid"] is True
    assert "abc123" not in stdout.getvalue()


def test_validate_fails_when_secret_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "request-validate-missing.db")

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
            created_at=BASE_TIME,
        )
    )
    runtime.build_integrity_notification_channel_service().create(
        "webhook",
        GovernanceIntegrityNotificationChannelType.WEBHOOK,
        "https://example.com/hook",
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_request_validate(
        channel_type="webhook",
        notification_id="n1",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
