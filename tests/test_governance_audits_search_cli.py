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


def latest_audit_id(env: dict[str, str]) -> str:
    payload = json.loads(
        run_cli(
            "governance", "audits", "replay", "--json", env=env
        ).stdout
    )
    return payload["audit_id"]


def test_search_by_healthy(tmp_path: Path) -> None:
    env = make_env(tmp_path, "search-healthy.db")

    run_cli("governance", "check", env=env)

    result = run_cli(
        "governance", "audits", "search", "--healthy", env=env
    )

    assert result.returncode == 0
    assert "Governance Audit Search" in result.stdout


def test_search_by_label(tmp_path: Path) -> None:
    env = make_env(tmp_path, "search-label.db")

    run_cli("governance", "check", env=env)
    audit_id = latest_audit_id(env)

    run_cli(
        "governance",
        "audits",
        "labels",
        "add",
        "--audit-id",
        audit_id,
        "--label",
        "release",
        env=env,
    )

    result = run_cli(
        "governance", "audits", "search", "--label", "release", env=env
    )

    assert result.returncode == 0
    assert audit_id in result.stdout


def test_search_by_bookmark(tmp_path: Path) -> None:
    env = make_env(tmp_path, "search-bookmark.db")

    run_cli("governance", "check", env=env)
    audit_id = latest_audit_id(env)

    run_cli(
        "governance",
        "audits",
        "bookmark",
        "add",
        "--name",
        "stable",
        "--audit-id",
        audit_id,
        env=env,
    )

    result = run_cli(
        "governance", "audits", "search", "--bookmark", "stable", env=env
    )

    assert result.returncode == 0
    assert audit_id in result.stdout


def test_search_combined_filters_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "search-combined.db")

    run_cli("governance", "check", env=env)
    audit_id = latest_audit_id(env)

    run_cli(
        "governance",
        "audits",
        "labels",
        "add",
        "--audit-id",
        audit_id,
        "--label",
        "release",
        env=env,
    )

    result = run_cli(
        "governance",
        "audits",
        "search",
        "--healthy",
        "--label",
        "release",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert isinstance(payload, list)


def test_search_requires_at_least_one_filter(tmp_path: Path) -> None:
    env = make_env(tmp_path, "search-no-filter.db")

    result = run_cli("governance", "audits", "search", env=env)

    assert result.returncode != 0
