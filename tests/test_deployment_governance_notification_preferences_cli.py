from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_notification_preferences_cli import (
    run_deployment_governance_notification_preference_create,
    run_deployment_governance_notification_preference_delete,
    run_deployment_governance_notification_preference_list,
    run_deployment_governance_notification_preference_show,
    run_deployment_governance_notification_preference_update,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_create_preference(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "preferences-create.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_preference_create(
        name="critical",
        minimum_severity="critical",
        channels=["slack"],
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Preference created" in output
    assert "Name: critical" in output
    assert "Minimum severity: critical" in output
    assert "Channels: slack" in output
    assert "Enabled: True" in output


def test_create_rejects_duplicate(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "preferences-create-dup.db")

    run_deployment_governance_notification_preference_create(
        name="critical",
        minimum_severity="critical",
        channels=["slack"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_notification_preference_create(
        name="critical",
        minimum_severity="warning",
        channels=["email"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "preferences-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_preference_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Notification Preferences" in stdout.getvalue()
    assert (
        "No governance audit notification preferences" in stdout.getvalue()
    )


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "preferences-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_notification_preference_show(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_update_channels(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "preferences-update.db")

    run_deployment_governance_notification_preference_create(
        name="critical",
        minimum_severity="critical",
        channels=["slack"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_preference_update(
        name="critical",
        channels=["email", "slack"],
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "Channels: email, slack" in stdout.getvalue()

    show_stdout = StringIO()

    run_deployment_governance_notification_preference_show(
        name="critical", stdout=show_stdout, stderr=StringIO()
    )

    assert "Channels: email, slack" in show_stdout.getvalue()


def test_update_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "preferences-update-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_notification_preference_update(
        name="missing",
        channels=["email"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_delete_preference(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "preferences-delete.db")

    run_deployment_governance_notification_preference_create(
        name="critical",
        minimum_severity="critical",
        channels=["slack"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_preference_delete(
        name="critical", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_stderr = StringIO()

    show_exit_code = run_deployment_governance_notification_preference_show(
        name="critical", stdout=StringIO(), stderr=show_stderr
    )

    assert show_exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "preferences-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_notification_preference_delete(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_create_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "preferences-create-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_notification_preference_create(
        name="warning-and-up",
        minimum_severity="warning",
        channels=["email"],
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["name"] == "warning-and-up"
    assert payload["minimum_severity"] == "warning"
    assert payload["channels"] == ["email"]
    assert payload["enabled"] is True
