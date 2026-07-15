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


def test_prune_without_limits_fails(tmp_path: Path) -> None:
    env = make_env(tmp_path, "prune-no-limits.db")

    result = run_cli("governance", "audits", "prune", env=env)

    assert result.returncode != 0


def test_prune_dry_run_by_default_performs_no_deletion(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "prune-dry-run.db")

    for _ in range(3):
        run_cli("governance", "check", env=env)

    result = run_cli(
        "governance",
        "audits",
        "prune",
        "--max-records",
        "10",
        env=env,
    )

    assert result.returncode == 0
    assert "DRY RUN" in result.stdout

    audits = run_cli("governance", "audits", "--json", env=env)
    payload = json.loads(audits.stdout)

    assert payload["summary"]["total_audits"] == 3


def test_prune_apply_deletes_planned_records(tmp_path: Path) -> None:
    env = make_env(tmp_path, "prune-apply.db")

    for _ in range(5):
        run_cli("governance", "check", env=env)

    result = run_cli(
        "governance",
        "audits",
        "prune",
        "--max-records",
        "2",
        "--apply",
        env=env,
    )

    assert result.returncode == 0
    assert "APPLIED" in result.stdout

    audits = run_cli("governance", "audits", "--json", env=env)
    payload = json.loads(audits.stdout)

    assert payload["summary"]["total_audits"] == 2


def test_prune_json_output_is_valid_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "prune-json.db")

    for _ in range(3):
        run_cli("governance", "check", env=env)

    result = run_cli(
        "governance",
        "audits",
        "prune",
        "--max-records",
        "10",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert "plan" in payload
    assert "applied" in payload
    assert payload["applied"] is False


def test_plain_audits_command_still_works_alongside_prune_subcommand(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "prune-plain-audits.db")

    run_cli("governance", "check", env=env)

    result = run_cli("governance", "audits", env=env)

    assert result.returncode == 0
    assert (
        "Deployment Governance Integrity Audit History"
        in result.stdout
    )
