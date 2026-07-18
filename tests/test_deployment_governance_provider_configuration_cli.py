from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_provider_configuration_cli import (
    parse_governance_provider_configuration_values,
    run_deployment_governance_provider_config_create,
    run_deployment_governance_provider_config_delete,
    run_deployment_governance_provider_config_list,
    run_deployment_governance_provider_config_show,
    run_deployment_governance_provider_config_update,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


# --- Option parsing --------------------------------------------------------


def test_parse_values_builds_mapping() -> None:
    assert parse_governance_provider_configuration_values(
        ["timeout=30", "sender=noreply@example.com"]
    ) == {"timeout": "30", "sender": "noreply@example.com"}


def test_parse_values_handles_none() -> None:
    assert parse_governance_provider_configuration_values(None) == {}


def test_parse_values_rejects_missing_equals() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_governance_provider_configuration_values(["timeout"])


# --- create ----------------------------------------------------------------


def test_create_human(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "config-create.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_config_create(
        channel_type="email",
        values=["timeout=30", "sender=noreply@example.com"],
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Channel type: email" in output
    assert "'timeout': '30'" in output


def test_create_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "config-create-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_config_create(
        channel_type="email",
        values=["timeout=30"],
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["channel_type"] == "email"
    assert payload["values"] == {"timeout": "30"}


def test_create_rejects_duplicate(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "config-create-duplicate.db")

    run_deployment_governance_provider_config_create(
        channel_type="email",
        values=["timeout=30"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_config_create(
        channel_type="email",
        values=["timeout=60"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


# --- list / show -------------------------------------------------------


def test_list_includes_created_configuration(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "config-list.db")

    run_deployment_governance_provider_config_create(
        channel_type="slack",
        values=["webhook_url=https://example.com/hook"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_config_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "slack:" in stdout.getvalue()


def test_show_returns_created_configuration(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "config-show.db")

    run_deployment_governance_provider_config_create(
        channel_type="webhook",
        values=["url=https://example.com"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_config_show(
        channel_type="webhook", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Channel type: webhook" in stdout.getvalue()


def test_show_fails_when_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "config-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_config_show(
        channel_type="email", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


# --- update ------------------------------------------------------------


def test_update_replaces_complete_configuration(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "config-update.db")

    run_deployment_governance_provider_config_create(
        channel_type="email",
        values=["timeout=30", "sender=a@example.com"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_config_update(
        channel_type="email",
        values=["timeout=60"],
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["values"] == {"timeout": "60"}

    show_stdout = StringIO()
    run_deployment_governance_provider_config_show(
        channel_type="email",
        json_output=True,
        stdout=show_stdout,
        stderr=StringIO(),
    )
    assert json.loads(show_stdout.getvalue())["values"] == {
        "timeout": "60"
    }


def test_update_fails_when_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "config-update-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_config_update(
        channel_type="email",
        values=["timeout=60"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


# --- delete ------------------------------------------------------------


def test_delete_removes_configuration(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "config-delete.db")

    run_deployment_governance_provider_config_create(
        channel_type="email",
        values=["timeout=30"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_config_delete(
        channel_type="email", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_stderr = StringIO()
    show_exit_code = run_deployment_governance_provider_config_show(
        channel_type="email", stdout=StringIO(), stderr=show_stderr
    )
    assert show_exit_code == 2
