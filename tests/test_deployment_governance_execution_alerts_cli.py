from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_execution_alerts_cli import (
    run_deployment_governance_execution_alerts,
    run_deployment_governance_execution_alerts_for_template,
)

# NOTE: the execution repository has no SQLite persistence
# (intentionally deferred, see deployment_governance_audit_worker.py),
# so each run_deployment_governance_execution_alerts* call bootstraps
# its own fresh, empty in-memory execution history. These tests
# exercise the empty-repository (no-violation) behavior of each
# command.


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_alerts_on_empty_history_with_default_thresholds(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "alerts-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_execution_alerts(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert "No governance audit execution alerts" in stdout.getvalue()


def test_alerts_json_output(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "alerts-empty-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_execution_alerts(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []


def test_alerts_rejects_invalid_policy(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "alerts-invalid-policy.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_execution_alerts(
        minimum_success_rate=150.0,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2


def test_alerts_for_template_on_empty_history(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "alerts-template-empty.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_execution_alerts_for_template(
        template_name="nightly", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Template: nightly" in output
    assert "No governance audit execution alerts" in output


def test_alerts_for_template_json_output(
    monkeypatch, tmp_path
) -> None:
    setup_env(monkeypatch, tmp_path, "alerts-template-empty-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_execution_alerts_for_template(
        template_name="nightly",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == []
