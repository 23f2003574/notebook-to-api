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
from backend.observability.deployment_governance_provider_registry_cli import (
    run_deployment_governance_provider_capabilities,
    run_deployment_governance_provider_disable,
    run_deployment_governance_provider_enable,
    run_deployment_governance_provider_health,
    run_deployment_governance_provider_health_all,
    run_deployment_governance_provider_list,
    run_deployment_governance_provider_metadata,
    run_deployment_governance_provider_replace,
    run_deployment_governance_provider_show,
    run_deployment_governance_provider_validate,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_list_includes_every_default_provider(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-list.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "email: EmailProvider" in output
    assert "slack: SlackProvider" in output
    assert "webhook: WebhookProvider" in output


def test_list_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-list-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_list(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert {
        "channel_type": "email",
        "provider_name": "EmailProvider",
    } in payload


def test_show_returns_registered_provider(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-show.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_show(
        channel_type="slack", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Channel type: slack" in output
    assert "Provider: SlackProvider" in output


def test_show_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-show-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_show(
        channel_type="webhook",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {
        "channel_type": "webhook",
        "provider_name": "WebhookProvider",
    }


def test_show_fails_for_invalid_channel_type(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-show-invalid.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_show(
        channel_type="not-a-real-channel-type",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_capabilities_human(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-capabilities.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_capabilities(
        channel_type="email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Channel type: email" in output
    assert "Supports retry: True" in output
    assert "Supports markdown: False" in output


def test_capabilities_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-capabilities-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_capabilities(
        channel_type="slack",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {
        "channel_type": "slack",
        "supports_retry": True,
        "supports_timeout": True,
        "supports_rate_limit": True,
        "supports_attachments": True,
        "supports_markdown": True,
    }


def test_validate_with_no_configured_channels(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-validate-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_validate(
        channel_type="email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "No delivery policies are configured" in stdout.getvalue()


def test_validate_with_compatible_policy(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-validate-compatible.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_notification_channel_service().create(
        "email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    runtime.build_integrity_delivery_policy_service().create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_validate(
        channel_type="email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "email: compatible" in stdout.getvalue()


def test_validate_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-validate-json.db")

    runtime = build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )

    runtime.build_integrity_notification_channel_service().create(
        "email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    runtime.build_integrity_delivery_policy_service().create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_validate(
        channel_type="email",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["channel_type"] == "email"
    assert payload["results"] == [
        {"channel_name": "email", "compatible": True, "error": None}
    ]


def test_health_human(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-health.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_health(
        channel_type="email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Channel type: email" in output
    assert "Status: healthy" in output


def test_health_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-health-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_health(
        channel_type="webhook",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["channel_type"] == "webhook"
    assert payload["status"] == "healthy"
    assert payload["message"] is None


def test_health_all_human(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-health-all.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_health_all(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "email: healthy" in output
    assert "slack: healthy" in output
    assert "webhook: healthy" in output


def test_health_all_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-health-all-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_health_all(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert len(payload) == 3
    assert all(record["status"] == "healthy" for record in payload)


def test_metadata_human(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-metadata.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_metadata(
        channel_type="email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Channel type: email" in output
    assert "Provider: EmailProvider" in output
    assert "State: enabled" in output


def test_metadata_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-metadata-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_metadata(
        channel_type="slack",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["channel_type"] == "slack"
    assert payload["provider_name"] == "SlackProvider"
    assert payload["state"] == "enabled"


def test_disable_then_metadata_reports_disabled(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-disable.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_disable(
        channel_type="email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "State: disabled" in stdout.getvalue()


def test_enable_human(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-enable.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_enable(
        channel_type="email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "State: enabled" in stdout.getvalue()


def test_replace_returns_same_provider_class(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-replace.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_replace(
        channel_type="webhook", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Channel type: webhook" in output
    assert "Provider: WebhookProvider" in output


def test_metadata_fails_for_invalid_channel_type(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-metadata-invalid.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_metadata(
        channel_type="not-a-real-channel-type",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()
