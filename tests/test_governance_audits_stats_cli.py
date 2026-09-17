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


def make_env(tmp_path: Path, name: str) -> dict[str, str]:
    env = dict(os.environ)
    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(tmp_path / name)
    return env


def test_audit_stats_command_handles_empty_history(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "stats-empty.db")

    result = run_cli("governance", "audits", "stats", env=env)

    assert result.returncode == 0
    assert "No governance integrity audits" in result.stdout


def test_audit_stats_command_renders_operational_summary(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "stats-populated.db")

    run_cli("governance", "check", env=env)
    run_cli("governance", "check", env=env)

    result = run_cli("governance", "audits", "stats", env=env)

    assert result.returncode == 0
    assert "Governance Audit History Statistics" in result.stdout
    assert "Health rate:" in result.stdout
    assert "Current streak:" in result.stdout
    assert "Aggregate Failures" in result.stdout


def test_audit_stats_command_emits_valid_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "stats-json.db")

    run_cli("governance", "check", env=env)

    result = run_cli(
        "governance", "audits", "stats", "--json", env=env
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert "total_audits" in payload
    assert "health_rate" in payload
    assert "current_state" in payload


def test_audit_stats_command_respects_limit(tmp_path: Path) -> None:
    env = make_env(tmp_path, "stats-limit.db")

    for _ in range(4):
        run_cli("governance", "check", env=env)

    result = run_cli(
        "governance",
        "audits",
        "stats",
        "--limit",
        "2",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["total_audits"] == 2


def test_audit_stats_command_rejects_non_positive_limit(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "stats-bad-limit.db")

    result = run_cli(
        "governance",
        "audits",
        "stats",
        "--limit",
        "0",
        env=env,
    )

    assert result.returncode == 2
