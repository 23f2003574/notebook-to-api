from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_failure_policy_cli import (
    run_deployment_governance_failure_policy_create,
    run_deployment_governance_failure_policy_delete,
    run_deployment_governance_failure_policy_list,
    run_deployment_governance_failure_policy_show,
    run_deployment_governance_failure_policy_update,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_create_policy(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-create.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_failure_policy_create(
        name="default",
        action="dead_letter",
        max_retry_attempts=2,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Policy created" in output
    assert "Name: default" in output
    assert "Action: dead_letter" in output
    assert "Max retry attempts: 2" in output


def test_create_rejects_duplicate(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-create-dup.db")

    run_deployment_governance_failure_policy_create(
        name="default",
        action="ignore",
        max_retry_attempts=0,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stderr = StringIO()

    exit_code = run_deployment_governance_failure_policy_create(
        name="default",
        action="ignore",
        max_retry_attempts=0,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_create_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-create-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_failure_policy_create(
        name="default",
        action="retry",
        max_retry_attempts=3,
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["name"] == "default"
    assert payload["action"] == "retry"
    assert payload["max_retry_attempts"] == 3


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_failure_policy_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Failure Policies" in stdout.getvalue()
    assert "No governance audit failure policies" in stdout.getvalue()


def test_list_returns_created_policies(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-list.db")

    run_deployment_governance_failure_policy_create(
        name="default",
        action="ignore",
        max_retry_attempts=0,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_failure_policy_list(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert len(payload) == 1
    assert payload[0]["name"] == "default"


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_failure_policy_show(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_show_existing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-show.db")

    run_deployment_governance_failure_policy_create(
        name="default",
        action="retry",
        max_retry_attempts=1,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_failure_policy_show(
        name="default", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Name: default" in stdout.getvalue()


def test_update_max_retries(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-update.db")

    run_deployment_governance_failure_policy_create(
        name="default",
        action="dead_letter",
        max_retry_attempts=2,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_failure_policy_update(
        name="default",
        max_retry_attempts=3,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "Max retry attempts: 3" in stdout.getvalue()

    show_stdout = StringIO()

    run_deployment_governance_failure_policy_show(
        name="default", stdout=show_stdout, stderr=StringIO()
    )

    assert "Max retry attempts: 3" in show_stdout.getvalue()


def test_update_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-update-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_failure_policy_update(
        name="missing",
        max_retry_attempts=1,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_delete_policy(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-delete.db")

    run_deployment_governance_failure_policy_create(
        name="default",
        action="ignore",
        max_retry_attempts=0,
        stdout=StringIO(),
        stderr=StringIO(),
    )

    stdout = StringIO()

    exit_code = run_deployment_governance_failure_policy_delete(
        name="default", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "deleted" in stdout.getvalue()

    show_stderr = StringIO()

    show_exit_code = run_deployment_governance_failure_policy_show(
        name="default", stdout=StringIO(), stderr=show_stderr
    )

    assert show_exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "policy-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_failure_policy_delete(
        name="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
