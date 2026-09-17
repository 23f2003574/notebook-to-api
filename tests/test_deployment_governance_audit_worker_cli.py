from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_audit_worker_cli import (
    run_deployment_governance_audit_worker_clear,
    run_deployment_governance_audit_worker_history,
    run_deployment_governance_audit_worker_run,
    run_deployment_governance_audit_worker_run_all,
    run_deployment_governance_audit_worker_show,
)

# NOTE: the execution queue and worker execution repository have no
# SQLite persistence (intentionally deferred, see
# deployment_governance_audit_worker.py), so each
# run_deployment_governance_audit_worker_* call bootstraps its own
# fresh, empty in-memory queue and history. These tests exercise each
# command's self-contained behavior rather than assuming state
# survives across separate calls -- schedules/templates/collections
# still persist via SQLite and are set up per test as needed.


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_run_missing_job(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "worker-run-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_worker_run(
        job_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()


def test_run_all_empty_queue(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "worker-run-all-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_worker_run_all(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_run_all_human_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "worker-run-all-human.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_worker_run_all(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Ran 0 job(s)" in stdout.getvalue()


def test_history_empty(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "worker-history-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_worker_history(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "Execution History" in stdout.getvalue()
    assert "No governance audit execution records" in stdout.getvalue()


def test_show_missing(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "worker-show-missing.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_audit_worker_show(
        job_id="missing", stdout=StringIO(), stderr=stderr
    )

    assert exit_code == 2


def test_clear_history(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "worker-clear.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_worker_clear(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "cleared" in stdout.getvalue()


def test_clear_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "worker-clear-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_audit_worker_clear(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["status"] == "cleared"
