from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_notification_channels_cli import (
    run_deployment_governance_notification_channel_create,
    run_deployment_governance_notification_channel_delete,
    run_deployment_governance_notification_channel_disable,
    run_deployment_governance_notification_channel_enable,
    run_deployment_governance_notification_channel_list,
    run_deployment_governance_notification_channel_show,
    run_deployment_governance_notification_channel_update,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_create_channel(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "channels-create.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_channel_create(
        name="ops-email",
        channel_type="email",
        destination="ops@example.com",
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Channel created" in output
    assert "Name: ops-email" in output
    assert "Type: email" in output
    assert "Destination: ops@example.com" in output
    assert "Enabled: True" in output


def test_create_rejects_duplicate(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "channels-create-dup.db")

    run_deployment_governance_notification_channel_create(
        name="ops-email",
        channel_type="email",
        destination="ops@example.com",
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_notification_channel_create(
        name="ops-email",
        channel_type="email",
        destination="other@example.com",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "channels-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_channel_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Notification Channels" in stdout.getvalue()
    assert (
        "No governance audit notification channels" in stdout.getvalue()
    )


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "channels-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_notification_channel_show(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_disable_then_show(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "channels-disable.db")

    run_deployment_governance_notification_channel_create(
        name="ops-email",
        channel_type="email",
        destination="ops@example.com",
        stdout=StringIO(),
        stderr=StringIO(),
    )

    disable_stdout = StringIO()

    exit_code = run_deployment_governance_notification_channel_disable(
        name="ops-email", stdout=disable_stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Enabled: False" in disable_stdout.getvalue()

    show_stdout = StringIO()

    run_deployment_governance_notification_channel_show(
        name="ops-email", stdout=show_stdout, stderr=StringIO()
    )

    assert "Enabled: False" in show_stdout.getvalue()


def test_enable_after_disable(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "channels-enable.db")

    run_deployment_governance_notification_channel_create(
        name="ops-email",
        channel_type="email",
        destination="ops@example.com",
        stdout=StringIO(),
        stderr=StringIO(),
    )
    run_deployment_governance_notification_channel_disable(
        name="ops-email", stdout=StringIO(), stderr=StringIO()
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_channel_enable(
        name="ops-email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Enabled: True" in stdout.getvalue()


def test_update_destination(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "channels-update.db")

    run_deployment_governance_notification_channel_create(
        name="ops-email",
        channel_type="email",
        destination="ops@example.com",
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_channel_update(
        name="ops-email",
        destination="admin@example.com",
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "Destination: admin@example.com" in stdout.getvalue()

    show_stdout = StringIO()

    run_deployment_governance_notification_channel_show(
        name="ops-email", stdout=show_stdout, stderr=StringIO()
    )

    assert "Destination: admin@example.com" in show_stdout.getvalue()


def test_delete_channel(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "channels-delete.db")

    run_deployment_governance_notification_channel_create(
        name="ops-email",
        channel_type="email",
        destination="ops@example.com",
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_channel_delete(
        name="ops-email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_stderr = StringIO()

    show_exit_code = run_deployment_governance_notification_channel_show(
        name="ops-email", stdout=StringIO(), stderr=show_stderr
    )

    assert show_exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "channels-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_notification_channel_delete(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_create_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "channels-create-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_channel_create(
        name="ops-slack",
        channel_type="slack",
        destination="#ops",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["name"] == "ops-slack"
    assert payload["channel_type"] == "slack"
    assert payload["destination"] == "#ops"
    assert payload["enabled"] is True
