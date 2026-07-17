from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_dead_letter_queue_cli import (
    run_deployment_governance_dead_letter_archive,
    run_deployment_governance_dead_letter_clear,
    run_deployment_governance_dead_letter_delete,
    run_deployment_governance_dead_letter_list,
    run_deployment_governance_dead_letter_show,
)

# NOTE: the execution repository and dead letter repository have no
# SQLite persistence (intentionally deferred, see
# deployment_governance_audit_worker.py and
# deployment_governance_dead_letter_queue.py), so each
# run_deployment_governance_dead_letter_* call bootstraps its own
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


def test_archive_missing_execution(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dlq-archive-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_dead_letter_archive(
        job_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_list_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dlq-list-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_dead_letter_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Dead Letter Queue" in stdout.getvalue()
    assert "No governance audit dead letter records" in stdout.getvalue()


def test_list_json_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dlq-list-json-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_dead_letter_list(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dlq-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_dead_letter_show(
        job_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_delete_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dlq-delete-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_dead_letter_delete(
        job_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_clear_queue(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dlq-clear.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_dead_letter_clear(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "cleared" in stdout.getvalue()


def test_clear_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "dlq-clear-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_dead_letter_clear(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["status"] == "cleared"
