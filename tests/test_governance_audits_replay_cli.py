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


def test_replay_command_defaults_to_latest(tmp_path: Path) -> None:
    env = make_env(tmp_path, "replay-latest.db")

    run_cli("governance", "check", env=env)

    result = run_cli("governance", "audits", "replay", env=env)

    assert result.returncode == 0
    assert "Governance Audit Replay" in result.stdout


def test_replay_command_emits_valid_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "replay-json.db")

    run_cli("governance", "check", env=env)

    result = run_cli(
        "governance", "audits", "replay", "--json", env=env
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert "audit_id" in payload
    assert "replayed_at" in payload
    assert "record" in payload


def test_replay_command_respects_limit(tmp_path: Path) -> None:
    env = make_env(tmp_path, "replay-limit.db")

    for _ in range(4):
        run_cli("governance", "check", env=env)

    result = run_cli(
        "governance",
        "audits",
        "replay",
        "--limit",
        "3",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert len(payload) == 3


def test_replay_command_limit_human_output(tmp_path: Path) -> None:
    env = make_env(tmp_path, "replay-limit-human.db")

    for _ in range(2):
        run_cli("governance", "check", env=env)

    result = run_cli(
        "governance", "audits", "replay", "--limit", "2", env=env
    )

    assert result.returncode == 0
    assert "Replay History" in result.stdout


def test_replay_command_rejects_missing_audit_id(tmp_path: Path) -> None:
    env = make_env(tmp_path, "replay-missing.db")

    run_cli("governance", "check", env=env)

    result = run_cli(
        "governance",
        "audits",
        "replay",
        "--audit-id",
        "missing",
        env=env,
    )

    assert result.returncode != 0


def test_replay_command_by_audit_id(tmp_path: Path) -> None:
    env = make_env(tmp_path, "replay-by-id.db")

    run_cli("governance", "check", env=env)

    latest = json.loads(
        run_cli(
            "governance", "audits", "replay", "--json", env=env
        ).stdout
    )

    audit_id = latest["audit_id"]

    result = run_cli(
        "governance",
        "audits",
        "replay",
        "--audit-id",
        audit_id,
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["audit_id"] == audit_id
