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


def test_bookmark_add_with_latest(tmp_path: Path) -> None:
    env = make_env(tmp_path, "bookmark-add-latest.db")

    run_cli("governance", "check", env=env)

    result = run_cli(
        "governance",
        "audits",
        "bookmark",
        "add",
        "--latest",
        "--name",
        "baseline",
        env=env,
    )

    assert result.returncode == 0
    assert "Bookmark created" in result.stdout


def test_bookmark_list(tmp_path: Path) -> None:
    env = make_env(tmp_path, "bookmark-list.db")

    run_cli("governance", "check", env=env)
    run_cli(
        "governance",
        "audits",
        "bookmark",
        "add",
        "--latest",
        "--name",
        "baseline",
        env=env,
    )

    result = run_cli(
        "governance", "audits", "bookmark", "list", env=env
    )

    assert result.returncode == 0
    assert "Bookmarks" in result.stdout
    assert "baseline" in result.stdout


def test_bookmark_show(tmp_path: Path) -> None:
    env = make_env(tmp_path, "bookmark-show.db")

    run_cli("governance", "check", env=env)
    run_cli(
        "governance",
        "audits",
        "bookmark",
        "add",
        "--latest",
        "--name",
        "baseline",
        env=env,
    )

    result = run_cli(
        "governance",
        "audits",
        "bookmark",
        "show",
        "--name",
        "baseline",
        env=env,
    )

    assert result.returncode == 0
    assert "Name: baseline" in result.stdout


def test_bookmark_delete(tmp_path: Path) -> None:
    env = make_env(tmp_path, "bookmark-delete.db")

    run_cli("governance", "check", env=env)
    run_cli(
        "governance",
        "audits",
        "bookmark",
        "add",
        "--latest",
        "--name",
        "baseline",
        env=env,
    )

    result = run_cli(
        "governance",
        "audits",
        "bookmark",
        "delete",
        "--name",
        "baseline",
        env=env,
    )

    assert result.returncode == 0

    show_result = run_cli(
        "governance",
        "audits",
        "bookmark",
        "show",
        "--name",
        "baseline",
        env=env,
    )

    assert show_result.returncode != 0


def test_bookmark_add_by_audit_id_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "bookmark-add-json.db")

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
        "bookmark",
        "add",
        "--name",
        "baseline",
        "--audit-id",
        audit_id,
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["name"] == "baseline"
    assert payload["audit_id"] == audit_id


def test_bookmark_add_rejects_duplicate_name(tmp_path: Path) -> None:
    env = make_env(tmp_path, "bookmark-duplicate.db")

    run_cli("governance", "check", env=env)
    run_cli(
        "governance",
        "audits",
        "bookmark",
        "add",
        "--latest",
        "--name",
        "baseline",
        env=env,
    )

    result = run_cli(
        "governance",
        "audits",
        "bookmark",
        "add",
        "--latest",
        "--name",
        "baseline",
        env=env,
    )

    assert result.returncode != 0
