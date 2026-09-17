from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_execution_metrics_cli import (
    run_deployment_governance_execution_metrics,
    run_deployment_governance_execution_metrics_for_template,
)

# NOTE: the execution repository has no SQLite persistence
# (intentionally deferred, see deployment_governance_audit_worker.py),
# so each run_deployment_governance_execution_metrics* call
# bootstraps its own fresh, empty in-memory execution history. These
# tests exercise the empty-repository behavior of each command.


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_metrics_on_empty_history(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_execution_metrics(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Execution Metrics" in output
    assert "Runs: 0" in output
    assert "Success: 0" in output
    assert "Failed: 0" in output
    assert "Success Rate: 0.00%" in output
    assert "Average Runtime: 0 ms" in output


def test_metrics_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-empty-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_execution_metrics(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["total_runs"] == 0
    assert payload["successful_runs"] == 0
    assert payload["failed_runs"] == 0
    assert payload["average_duration_ms"] == 0.0
    assert payload["success_rate"] == 0.0


def test_metrics_for_template_on_empty_history(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-template-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_execution_metrics_for_template(
        template_name="nightly", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Template: nightly" in output
    assert "Runs: 0" in output


def test_metrics_for_template_json_output(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "metrics-template-empty-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_execution_metrics_for_template(
        template_name="nightly",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0

    payload = json.loads(stdout.getvalue())

    assert payload["total_runs"] == 0
