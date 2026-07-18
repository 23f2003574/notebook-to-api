from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_provider_secrets_cli import (
    run_deployment_governance_provider_secrets_create,
    run_deployment_governance_provider_secrets_delete,
    run_deployment_governance_provider_secrets_list,
    run_deployment_governance_provider_secrets_show,
    run_deployment_governance_provider_secrets_update,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


# --- create ----------------------------------------------------------------


def test_create_human(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "secrets-create.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_secrets_create(
        channel_type="webhook",
        values=["api_key=abc123"],
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Channel type: webhook" in output
    assert "'api_key'" in output
    assert "abc123" not in output


def test_create_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "secrets-create-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_secrets_create(
        channel_type="webhook",
        values=["api_key=abc123"],
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["channel_type"] == "webhook"
    assert payload["values"] == {"api_key": "abc123"}


def test_create_rejects_duplicate(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "secrets-create-duplicate.db")

    run_deployment_governance_provider_secrets_create(
        channel_type="webhook",
        values=["api_key=abc123"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_secrets_create(
        channel_type="webhook",
        values=["api_key=xyz789"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


# --- list / show -------------------------------------------------------


def test_list_includes_created_secrets(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "secrets-list.db")

    run_deployment_governance_provider_secrets_create(
        channel_type="webhook",
        values=["api_key=abc123"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_secrets_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "webhook:" in output
    assert "abc123" not in output


def test_show_returns_created_secrets(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "secrets-show.db")

    run_deployment_governance_provider_secrets_create(
        channel_type="webhook",
        values=["api_key=abc123"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_secrets_show(
        channel_type="webhook", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Channel type: webhook" in stdout.getvalue()


def test_show_json_returns_full_values(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "secrets-show-json.db")

    run_deployment_governance_provider_secrets_create(
        channel_type="webhook",
        values=["api_key=abc123"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_secrets_show(
        channel_type="webhook",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["values"] == {
        "api_key": "abc123"
    }


def test_show_fails_when_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "secrets-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_secrets_show(
        channel_type="webhook", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


# --- update ------------------------------------------------------------


def test_update_replaces_complete_secret_set(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "secrets-update.db")

    run_deployment_governance_provider_secrets_create(
        channel_type="webhook",
        values=["api_key=abc123", "webhook_signing_secret=sig"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_secrets_update(
        channel_type="webhook",
        values=["api_key=xyz789"],
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["values"] == {"api_key": "xyz789"}

    show_stdout = StringIO()
    run_deployment_governance_provider_secrets_show(
        channel_type="webhook",
        json_output=True,
        stdout=show_stdout,
        stderr=StringIO(),
    )
    assert json.loads(show_stdout.getvalue())["values"] == {
        "api_key": "xyz789"
    }


def test_update_fails_when_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "secrets-update-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_secrets_update(
        channel_type="webhook",
        values=["api_key=xyz789"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


# --- delete ------------------------------------------------------------


def test_delete_removes_secrets(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "secrets-delete.db")

    run_deployment_governance_provider_secrets_create(
        channel_type="webhook",
        values=["api_key=abc123"],
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_secrets_delete(
        channel_type="webhook", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_stderr = StringIO()
    show_exit_code = run_deployment_governance_provider_secrets_show(
        channel_type="webhook", stdout=StringIO(), stderr=show_stderr
    )
    assert show_exit_code == 2
