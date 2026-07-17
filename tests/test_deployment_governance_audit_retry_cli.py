from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_audit_retry_cli import (
    run_deployment_governance_audit_retry_clear,
    run_deployment_governance_audit_retry_history,
    run_deployment_governance_audit_retry_run,
    run_deployment_governance_audit_retry_show,
)

# NOTE: the execution queue, execution repository, and retry
# repository have no SQLite persistence (intentionally deferred, see
# deployment_governance_audit_worker.py and
# deployment_governance_audit_retry.py), so each
# run_deployment_governance_audit_retry_* call bootstraps its own
# fresh, empty in-memory state. These tests exercise each command's
# self-contained behavior rather than assuming state survives across
# separate calls.


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_run_missing_execution(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "retry-run-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_retry_run(
        job_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_history_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "retry-history-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_retry_history(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Retry History" in stdout.getvalue()
    assert "No governance audit retry records" in stdout.getvalue()


def test_history_json_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "retry-history-json-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_retry_history(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "retry-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_retry_show(
        job_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_clear_history(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "retry-clear.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_retry_clear(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "cleared" in stdout.getvalue()


def test_clear_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "retry-clear-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_retry_clear(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["status"] == "cleared"
