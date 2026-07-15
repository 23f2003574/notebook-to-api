from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def run_cli(
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.cli",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parent.parent,
    )


def test_governance_check_command_exists(
    tmp_path: Path,
) -> None:
    env = dict(os.environ)

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(
        tmp_path / "check-cli-exists.db"
    )

    result = run_cli("governance", "check", env=env)

    assert result.returncode == 0

    assert (
        "Deployment Governance Integrity Check"
        in result.stdout
    )


def test_governance_check_json_output(
    tmp_path: Path,
) -> None:
    env = dict(os.environ)

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(
        tmp_path / "check-cli-json.db"
    )

    result = run_cli("governance", "check", "--json", env=env)

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["passed"] is True
    assert "regression" in payload
    assert result.stderr == ""


def test_governance_check_require_healthy_policy_option(
    tmp_path: Path,
) -> None:
    env = dict(os.environ)

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(
        tmp_path / "check-cli-require-healthy.db"
    )

    result = run_cli(
        "governance",
        "check",
        "--policy",
        "require-healthy",
        env=env,
    )

    assert result.returncode == 0
    assert "Policy: require_healthy" in result.stdout


def test_governance_check_rejects_invalid_policy() -> None:
    result = run_cli("governance", "check", "--policy", "bogus")

    assert result.returncode != 0


def test_governance_check_rejects_invalid_batch_size() -> None:
    result = run_cli(
        "governance", "check", "--batch-size", "0"
    )

    assert result.returncode != 0


def test_governance_check_execution_failure_on_memory_backend(
    monkeypatch,
) -> None:
    env = dict(os.environ)

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "memory"

    result = run_cli("governance", "check", env=env)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "could not be executed" in result.stderr


def test_governance_check_exits_three_on_regression(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "check-cli-regression.db"

    env = dict(os.environ)

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(database_path)

    first = run_cli("governance", "check", env=env)
    assert first.returncode == 0

    connection = sqlite3.connect(str(database_path))
    connection.execute(
        """
        INSERT INTO deployment_governance_traces (
            trace_id, deployment_id, service_name, environment,
            artifact_digest, created_at, updated_at, governance_state,
            final_status, completed, payload
        ) VALUES (
            'trace-check-cli-regression', 'deployment-check-cli-regression',
            'payments-api', 'staging', 'sha256:regression',
            '2026-07-15T00:00:00+00:00', '2026-07-15T00:00:00+00:00',
            'created', NULL, 0, '{}'
        )
        """
    )
    connection.commit()
    connection.close()

    second = run_cli("governance", "check", "--json", env=env)

    assert second.returncode == 3

    payload = json.loads(second.stdout)

    assert payload["status"] == "regression_detected"
    assert payload["passed"] is False
