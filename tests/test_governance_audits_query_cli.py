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


def test_query_save_run_list_show_delete(tmp_path: Path) -> None:
    env = make_env(tmp_path, "query-lifecycle.db")

    run_cli("governance", "check", env=env)

    save_result = run_cli(
        "governance",
        "audits",
        "query",
        "save",
        "--name",
        "healthy",
        "--healthy",
        env=env,
    )

    assert save_result.returncode == 0
    assert "Saved Query" in save_result.stdout

    run_result = run_cli(
        "governance", "audits", "query", "run", "--name", "healthy",
        env=env,
    )

    assert run_result.returncode == 0
    assert "Governance Audit Search" in run_result.stdout

    list_result = run_cli(
        "governance", "audits", "query", "list", env=env
    )

    assert list_result.returncode == 0
    assert "healthy" in list_result.stdout

    show_result = run_cli(
        "governance", "audits", "query", "show", "--name", "healthy",
        env=env,
    )

    assert show_result.returncode == 0
    assert "Name: healthy" in show_result.stdout

    delete_result = run_cli(
        "governance", "audits", "query", "delete", "--name", "healthy",
        env=env,
    )

    assert delete_result.returncode == 0

    show_after_delete = run_cli(
        "governance", "audits", "query", "show", "--name", "healthy",
        env=env,
    )

    assert show_after_delete.returncode != 0


def test_query_run_json_matches_search(tmp_path: Path) -> None:
    env = make_env(tmp_path, "query-json.db")

    run_cli("governance", "check", env=env)

    run_cli(
        "governance",
        "audits",
        "query",
        "save",
        "--name",
        "healthy",
        "--healthy",
        env=env,
    )

    query_result = run_cli(
        "governance", "audits", "query", "run", "--name", "healthy",
        "--json", env=env,
    )
    search_result = run_cli(
        "governance", "audits", "search", "--healthy", "--json",
        env=env,
    )

    assert query_result.returncode == 0
    assert json.loads(query_result.stdout) == json.loads(
        search_result.stdout
    )
