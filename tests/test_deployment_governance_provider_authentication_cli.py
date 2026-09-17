from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)
from backend.observability.deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from backend.observability.deployment_governance_provider_authentication_cli import (
    run_deployment_governance_provider_auth_show,
    run_deployment_governance_provider_auth_validate,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def _seed_webhook_api_key(monkeypatch, tmp_path, name: str) -> None:
    setup_env(monkeypatch, tmp_path, name)

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_provider_secrets_service().create(
        GovernanceIntegrityNotificationChannelType.WEBHOOK,
        {"api_key": "abc123"},
    )


# --- show ------------------------------------------------------------------


def test_show_redacts_header_values(monkeypatch, tmp_path) -> None:
    _seed_webhook_api_key(monkeypatch, tmp_path, "auth-show.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_auth_show(
        channel_type="webhook", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Channel type: webhook" in output
    assert "Authentication type: api_key" in output
    assert "abc123" not in output


def test_show_json_redacts_header_values(monkeypatch, tmp_path) -> None:
    _seed_webhook_api_key(monkeypatch, tmp_path, "auth-show-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_auth_show(
        channel_type="webhook",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["channel_type"] == "webhook"
    assert payload["authentication_type"] == "api_key"
    assert payload["headers"] == {"X-API-Key": "***REDACTED***"}
    assert "abc123" not in stdout.getvalue()


def test_show_human_for_none_authentication(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "auth-show-none.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_auth_show(
        channel_type="email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Authentication type: none" in output
    assert "Header names: []" in output


def test_show_fails_when_required_secret_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "auth-show-missing-secret.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_auth_show(
        channel_type="webhook", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()
    assert "abc123" not in stderr.getvalue()


# --- validate ------------------------------------------------------------


def test_validate_succeeds_for_none_authentication(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "auth-validate-none.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_auth_validate(
        channel_type="email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "can be built successfully" in stdout.getvalue()


def test_validate_succeeds_with_seeded_secret(monkeypatch, tmp_path) -> None:
    _seed_webhook_api_key(monkeypatch, tmp_path, "auth-validate-ok.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_auth_validate(
        channel_type="webhook",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["valid"] is True
    assert "abc123" not in stdout.getvalue()


def test_validate_fails_when_required_secret_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "auth-validate-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_auth_validate(
        channel_type="webhook", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()
