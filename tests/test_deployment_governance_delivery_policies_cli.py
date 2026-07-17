from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_delivery_policies_cli import (
    run_deployment_governance_delivery_policy_create,
    run_deployment_governance_delivery_policy_delete,
    run_deployment_governance_delivery_policy_list,
    run_deployment_governance_delivery_policy_show,
    run_deployment_governance_delivery_policy_update,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def create_channel(monkeypatch, tmp_path, db_name: str, channel_name: str) -> None:
    from backend.observability.deployment_governance_notification_channels import (
        GovernanceIntegrityNotificationChannelType,
    )
    from backend.observability.deployment_governance_persistence import (
        DeploymentGovernancePersistenceConfig,
        build_deployment_governance_persistence,
    )

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(tmp_path / db_name)
    )

    runtime.build_integrity_notification_channel_service().create(
        channel_name,
        GovernanceIntegrityNotificationChannelType.EMAIL,
        f"dest-{channel_name}",
    )


def test_create_policy(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-policy-create.db")

    create_channel(
        monkeypatch, tmp_path, "delivery-policy-create.db", "ops-email"
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_policy_create(
        channel_name="ops-email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Policy created" in output
    assert "Channel: ops-email" in output
    assert "Retry limit: 3" in output
    assert "Timeout seconds: 30" in output
    assert "Rate limit per minute: 60" in output
    assert "Enabled: True" in output


def test_create_rejects_missing_channel(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-policy-create-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_delivery_policy_create(
        channel_name="missing",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_create_rejects_duplicate(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-policy-create-dup.db")

    create_channel(
        monkeypatch, tmp_path, "delivery-policy-create-dup.db", "ops-email"
    )

    run_deployment_governance_delivery_policy_create(
        channel_name="ops-email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_delivery_policy_create(
        channel_name="ops-email",
        retry_limit=5,
        timeout_seconds=45,
        rate_limit_per_minute=30,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-policy-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_policy_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Delivery Policies" in stdout.getvalue()
    assert "No governance audit delivery policies" in stdout.getvalue()


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-policy-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_delivery_policy_show(
        channel_name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_update_timeout(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-policy-update.db")

    create_channel(
        monkeypatch, tmp_path, "delivery-policy-update.db", "ops-email"
    )

    run_deployment_governance_delivery_policy_create(
        channel_name="ops-email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_policy_update(
        channel_name="ops-email",
        timeout_seconds=45,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "Timeout seconds: 45" in stdout.getvalue()

    show_stdout = StringIO()

    run_deployment_governance_delivery_policy_show(
        channel_name="ops-email", stdout=show_stdout, stderr=StringIO()
    )

    assert "Timeout seconds: 45" in show_stdout.getvalue()


def test_update_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-policy-update-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_delivery_policy_update(
        channel_name="missing",
        timeout_seconds=45,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_delete_policy(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-policy-delete.db")

    create_channel(
        monkeypatch, tmp_path, "delivery-policy-delete.db", "ops-email"
    )

    run_deployment_governance_delivery_policy_create(
        channel_name="ops-email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_policy_delete(
        channel_name="ops-email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_stderr = StringIO()

    show_exit_code = run_deployment_governance_delivery_policy_show(
        channel_name="ops-email", stdout=StringIO(), stderr=show_stderr
    )

    assert show_exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-policy-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_delivery_policy_delete(
        channel_name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_create_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "delivery-policy-create-json.db")

    create_channel(
        monkeypatch, tmp_path, "delivery-policy-create-json.db", "ops-email"
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_delivery_policy_create(
        channel_name="ops-email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["channel_name"] == "ops-email"
    assert payload["retry_limit"] == 3
    assert payload["timeout_seconds"] == 30
    assert payload["rate_limit_per_minute"] == 60
    assert payload["enabled"] is True
