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


def test_session_command_handles_empty_history(tmp_path: Path) -> None:
    env = make_env(tmp_path, "session-empty.db")

    result = run_cli("governance", "audits", "session", env=env)

    assert result.returncode == 0
    assert "Governance Audit Session" in result.stdout
    assert "No governance integrity audits" in result.stdout


def test_session_command_succeeds_with_history(tmp_path: Path) -> None:
    env = make_env(tmp_path, "session-populated.db")

    run_cli("governance", "check", env=env)
    run_cli("governance", "check", env=env)

    result = run_cli("governance", "audits", "session", env=env)

    assert result.returncode == 0
    assert "Governance Audit Session" in result.stdout
    assert "History" in result.stdout


def test_session_command_emits_valid_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "session-json.db")

    run_cli("governance", "check", env=env)

    result = run_cli(
        "governance", "audits", "session", "--json", env=env
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["total_audits"] == 1
    assert "latest_audit_id" in payload
    assert "first_audit_id" in payload
    assert "records" in payload


def test_session_command_respects_limit(tmp_path: Path) -> None:
    env = make_env(tmp_path, "session-limit.db")

    for _ in range(3):
        run_cli("governance", "check", env=env)

    result = run_cli(
        "governance",
        "audits",
        "session",
        "--limit",
        "3",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["total_audits"] == 3
