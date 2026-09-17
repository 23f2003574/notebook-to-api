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


def test_diff_command_defaults_to_latest_pair(tmp_path: Path) -> None:
    env = make_env(tmp_path, "diff-latest.db")

    run_cli("governance", "check", env=env)
    run_cli("governance", "check", env=env)

    result = run_cli("governance", "audits", "diff", env=env)

    assert result.returncode == 0
    assert "Governance Audit Diff" in result.stdout


def test_diff_command_emits_valid_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "diff-json.db")

    run_cli("governance", "check", env=env)
    run_cli("governance", "check", env=env)

    result = run_cli(
        "governance", "audits", "diff", "--json", env=env
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert "changed" in payload
    assert "field_diffs" in payload


def test_diff_command_by_audit_id(tmp_path: Path) -> None:
    env = make_env(tmp_path, "diff-by-id.db")

    run_cli("governance", "check", env=env)
    run_cli("governance", "check", env=env)

    replays = json.loads(
        run_cli(
            "governance", "audits", "replay", "--limit", "2", "--json",
            env=env,
        ).stdout
    )

    current_id = replays[0]["audit_id"]
    previous_id = replays[1]["audit_id"]

    result = run_cli(
        "governance",
        "audits",
        "diff",
        "--previous",
        previous_id,
        "--current",
        current_id,
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["previous_audit_id"] == previous_id
    assert payload["current_audit_id"] == current_id


def test_diff_command_rejects_missing_audit(tmp_path: Path) -> None:
    env = make_env(tmp_path, "diff-missing.db")

    run_cli("governance", "check", env=env)

    result = run_cli(
        "governance",
        "audits",
        "diff",
        "--previous",
        "missing",
        "--current",
        "latest",
        env=env,
    )

    assert result.returncode != 0


def test_diff_command_requires_previous_and_current_together(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "diff-partial.db")

    result = run_cli(
        "governance",
        "audits",
        "diff",
        "--previous",
        "some-id",
        env=env,
    )

    assert result.returncode != 0
