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


def test_timeline_command_handles_empty_history(tmp_path: Path) -> None:
    env = make_env(tmp_path, "timeline-empty.db")

    result = run_cli("governance", "audits", "timeline", env=env)

    assert result.returncode == 0
    assert "Governance Audit Timeline" in result.stdout
    assert "No governance integrity audits" in result.stdout


def test_timeline_command_succeeds_with_history(tmp_path: Path) -> None:
    env = make_env(tmp_path, "timeline-populated.db")

    run_cli("governance", "check", env=env)
    run_cli("governance", "check", env=env)

    result = run_cli("governance", "audits", "timeline", env=env)

    assert result.returncode == 0
    assert "Governance Audit Timeline" in result.stdout


def test_timeline_command_emits_valid_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "timeline-json.db")

    run_cli("governance", "check", env=env)

    result = run_cli(
        "governance", "audits", "timeline", "--json", env=env
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert isinstance(payload, list)
    assert "audit_id" in payload[0]
    assert "state" in payload[0]


def test_timeline_command_respects_limit(tmp_path: Path) -> None:
    env = make_env(tmp_path, "timeline-limit.db")

    for _ in range(5):
        run_cli("governance", "check", env=env)

    result = run_cli(
        "governance",
        "audits",
        "timeline",
        "--limit",
        "5",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert len(payload) == 5
