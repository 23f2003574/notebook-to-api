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


def test_collections_full_lifecycle(tmp_path: Path) -> None:
    env = make_env(tmp_path, "collections-lifecycle.db")

    run_cli("governance", "check", env=env)
    audit_id = latest_audit_id(env)

    create_result = run_cli(
        "governance",
        "audits",
        "collections",
        "create",
        "--name",
        "release-v1",
        env=env,
    )

    assert create_result.returncode == 0
    assert "Collection created" in create_result.stdout

    add_result = run_cli(
        "governance",
        "audits",
        "collections",
        "add",
        "--name",
        "release-v1",
        "--audit-id",
        audit_id,
        env=env,
    )

    assert add_result.returncode == 0

    show_result = run_cli(
        "governance",
        "audits",
        "collections",
        "show",
        "--name",
        "release-v1",
        env=env,
    )

    assert show_result.returncode == 0
    assert audit_id in show_result.stdout

    list_result = run_cli(
        "governance", "audits", "collections", "list", env=env
    )

    assert list_result.returncode == 0
    assert "release-v1" in list_result.stdout

    remove_result = run_cli(
        "governance",
        "audits",
        "collections",
        "remove",
        "--name",
        "release-v1",
        "--audit-id",
        audit_id,
        env=env,
    )

    assert remove_result.returncode == 0

    delete_result = run_cli(
        "governance",
        "audits",
        "collections",
        "delete",
        "--name",
        "release-v1",
        env=env,
    )

    assert delete_result.returncode == 0

    show_after_delete = run_cli(
        "governance",
        "audits",
        "collections",
        "show",
        "--name",
        "release-v1",
        env=env,
    )

    assert show_after_delete.returncode != 0


def test_collections_show_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "collections-json.db")

    run_cli("governance", "check", env=env)
    audit_id = latest_audit_id(env)

    run_cli(
        "governance",
        "audits",
        "collections",
        "create",
        "--name",
        "release-v1",
        env=env,
    )
    run_cli(
        "governance",
        "audits",
        "collections",
        "add",
        "--name",
        "release-v1",
        "--audit-id",
        audit_id,
        env=env,
    )

    result = run_cli(
        "governance",
        "audits",
        "collections",
        "show",
        "--name",
        "release-v1",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["name"] == "release-v1"
    assert payload["audits"] == [audit_id]
