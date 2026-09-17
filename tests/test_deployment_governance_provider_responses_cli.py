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
from backend.observability.deployment_governance_provider_responses_cli import (
    run_deployment_governance_provider_response_show,
    run_deployment_governance_provider_response_validate,
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


def _seed_queued_dispatch(monkeypatch, tmp_path, name: str) -> str:
    setup_env(monkeypatch, tmp_path, name)

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
        "email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    runtime.build_integrity_notification_preference_service().create(
        "warning-and-up",
        GovernanceIntegrityAlertSeverity.WARNING,
        ("email",),
    )

    dispatches = (
        runtime.build_integrity_notification_dispatcher()
        .dispatch_pending()
    )

    assert len(dispatches) == 1

    return dispatches[0].dispatch_id


# --- show ------------------------------------------------------------------


def test_show_reports_response_and_outcome(monkeypatch, tmp_path) -> None:
    dispatch_id = _seed_queued_dispatch(
        monkeypatch, tmp_path, "response-show.db"
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_response_show(
        dispatch_id=dispatch_id, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Status code: 200" in output
    assert "Success: True" in output


def test_show_json(monkeypatch, tmp_path) -> None:
    dispatch_id = _seed_queued_dispatch(
        monkeypatch, tmp_path, "response-show-json.db"
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_response_show(
        dispatch_id=dispatch_id,
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["response"]["status_code"] == 200
    assert payload["outcome"]["success"] is True


def test_show_fails_for_missing_dispatch(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "response-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_response_show(
        dispatch_id="does-not-exist", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


# --- validate ------------------------------------------------------------


def test_validate_reports_success(monkeypatch, tmp_path) -> None:
    dispatch_id = _seed_queued_dispatch(
        monkeypatch, tmp_path, "response-validate.db"
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_response_validate(
        dispatch_id=dispatch_id,
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["valid"] is True
    assert payload["success"] is True
    assert payload["retryable"] is False


def test_validate_fails_for_missing_dispatch(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "response-validate-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_response_validate(
        dispatch_id="does-not-exist", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
