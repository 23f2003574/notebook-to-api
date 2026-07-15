from __future__ import annotations

import json
import os
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


def test_governance_audits_command_exists(monkeypatch) -> None:
    monkeypatch.delenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND",
        raising=False,
    )

    result = run_cli(
        "governance",
        "audits",
    )

    assert result.returncode == 0

    assert (
        "Deployment Governance Integrity Audit History"
        in result.stdout
    )


def test_governance_audits_json_output() -> None:
    result = run_cli(
        "governance",
        "audits",
        "--json",
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert "summary" in payload
    assert "records" in payload

    assert result.stderr == ""


def test_governance_audits_rejects_invalid_outcome() -> None:
    result = run_cli(
        "governance",
        "audits",
        "--outcome",
        "bogus",
    )

    assert result.returncode != 0


def test_governance_audits_rejects_invalid_timestamp(
    tmp_path: Path,
) -> None:
    env = dict(os.environ)

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(
        tmp_path / "audits-cli-bad-timestamp.db"
    )

    result = run_cli(
        "governance",
        "audits",
        "--since",
        "not-a-timestamp",
        env=env,
    )

    assert result.returncode != 0


def test_governance_audits_can_render_trend_analysis() -> None:
    result = run_cli(
        "governance",
        "audits",
        "--trend",
    )

    assert result.returncode == 0

    assert "Trend Analysis" in result.stdout
    assert "Direction:" in result.stdout


def test_governance_audits_can_emit_trend_json() -> None:
    result = run_cli(
        "governance",
        "audits",
        "--trend",
        "--json",
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert "trend" in payload
    assert "direction" in payload["trend"]


def test_governance_audits_json_without_trend_omits_trend_key() -> None:
    result = run_cli(
        "governance",
        "audits",
        "--json",
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert "trend" not in payload


def test_governance_audits_rejects_non_positive_trend_window() -> None:
    result = run_cli(
        "governance",
        "audits",
        "--trend",
        "--trend-window",
        "0",
    )

    assert result.returncode != 0


def test_governance_doctor_deep_then_audits_reports_trend(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "audits-cli-trend.db"

    env = dict(os.environ)

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(database_path)

    run_cli("governance", "doctor", "--deep", env=env)
    run_cli("governance", "doctor", "--deep", env=env)

    result = run_cli(
        "governance",
        "audits",
        "--trend",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["trend"]["sample_size"] == 2
    assert payload["trend"]["direction"] == "stable"
    assert payload["trend"]["current_streak"] == 2


def test_governance_audits_can_render_regression_analysis() -> None:
    result = run_cli(
        "governance",
        "audits",
        "--regression",
    )

    assert result.returncode == 0

    assert "Regression Analysis" in result.stdout
    assert "Regression detected:" in result.stdout


def test_governance_audits_can_emit_regression_json() -> None:
    result = run_cli(
        "governance",
        "audits",
        "--regression",
        "--json",
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert "regression" in payload
    assert "status" in payload["regression"]
    assert "regression_detected" in payload["regression"]


def test_governance_audits_json_without_regression_omits_regression_key() -> None:
    result = run_cli(
        "governance",
        "audits",
        "--json",
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert "regression" not in payload


def test_governance_audits_supports_trend_and_regression_together(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "audits-cli-trend-and-regression.db"

    env = dict(os.environ)

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(database_path)

    run_cli("governance", "doctor", "--deep", env=env)
    run_cli("governance", "doctor", "--deep", env=env)

    result = run_cli(
        "governance",
        "audits",
        "--trend",
        "--regression",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert set(payload.keys()) == {
        "summary",
        "records",
        "trend",
        "regression",
    }

    # doctor --deep does not fail exit code even when regression is found;
    # this is inspection only, per the "no --fail-on-regression yet" scope.
    assert payload["regression"]["status"] == "healthy"


def test_governance_audits_regression_exit_code_stays_zero_on_detection(
    tmp_path: Path,
) -> None:
    # governance audits --regression is inspection-only: even when it
    # detects a genuine regression, the command still exits 0 because the
    # query itself succeeded. A CI enforcement mode is a separate concern.
    import sqlite3

    database_path = tmp_path / "audits-cli-regression-detected.db"

    env = dict(os.environ)

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(database_path)

    first = run_cli("governance", "doctor", "--deep", env=env)
    assert first.returncode == 0

    connection = sqlite3.connect(str(database_path))
    connection.execute(
        """
        INSERT INTO deployment_governance_traces (
            trace_id, deployment_id, service_name, environment,
            artifact_digest, created_at, updated_at, governance_state,
            final_status, completed, payload
        ) VALUES (
            'trace-regression', 'deployment-regression', 'payments-api',
            'staging', 'sha256:regression', '2026-07-15T00:00:00+00:00',
            '2026-07-15T00:00:00+00:00', 'created', NULL, 0, '{}'
        )
        """
    )
    connection.commit()
    connection.close()

    second_doctor = run_cli(
        "governance", "doctor", "--deep", env=env
    )
    assert second_doctor.returncode == 1  # unhealthy, but audits succeeds

    second = run_cli(
        "governance",
        "audits",
        "--regression",
        "--json",
        env=env,
    )

    assert second.returncode == 0

    payload = json.loads(second.stdout)

    assert payload["regression"]["status"] == "regression"
    assert payload["regression"]["regression_detected"] is True


def test_governance_doctor_deep_then_audits_lists_recorded_audit(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "audits-cli-e2e.db"

    env = dict(os.environ)

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(database_path)

    initial = run_cli(
        "governance",
        "audits",
        "--json",
        env=env,
    )

    assert initial.returncode == 0

    initial_payload = json.loads(initial.stdout)

    assert initial_payload["summary"]["total_audits"] == 0

    doctor_result = run_cli(
        "governance",
        "doctor",
        "--deep",
        env=env,
    )

    assert doctor_result.returncode == 0

    second_doctor_result = run_cli(
        "governance",
        "doctor",
        "--deep",
        env=env,
    )

    assert second_doctor_result.returncode == 0

    limited = run_cli(
        "governance",
        "audits",
        "--limit",
        "1",
        "--json",
        env=env,
    )

    assert limited.returncode == 0

    limited_payload = json.loads(limited.stdout)

    assert limited_payload["summary"]["total_audits"] == 2
    assert len(limited_payload["records"]) == 1

    healthy_only = run_cli(
        "governance",
        "audits",
        "--outcome",
        "healthy",
        env=env,
    )

    assert healthy_only.returncode == 0
    assert "Returned audits: 2" in healthy_only.stdout

    unhealthy_only = run_cli(
        "governance",
        "audits",
        "--outcome",
        "unhealthy",
        env=env,
    )

    assert unhealthy_only.returncode == 0
    assert "No matching integrity audits found." in unhealthy_only.stdout
