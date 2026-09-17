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


def test_templates_full_lifecycle(tmp_path: Path) -> None:
    env = make_env(tmp_path, "templates-lifecycle.db")

    run_cli("governance", "check", env=env)
    audit_id = latest_audit_id(env)

    run_cli(
        "governance", "audits", "collections", "create",
        "--name", "release-v1", env=env,
    )
    run_cli(
        "governance", "audits", "collections", "add",
        "--name", "release-v1", "--audit-id", audit_id, env=env,
    )

    create_result = run_cli(
        "governance", "audits", "templates", "create",
        "--name", "release",
        "--title", "Release Report",
        "--collection", "release-v1",
        env=env,
    )

    assert create_result.returncode == 0
    assert "Template created" in create_result.stdout

    generate_result = run_cli(
        "governance", "audits", "templates", "generate",
        "--name", "release", env=env,
    )

    assert generate_result.returncode == 0

    payload = json.loads(generate_result.stdout)

    assert payload["title"] == "Release Report"
    assert payload["audits"][0]["audit_id"] == audit_id

    list_result = run_cli(
        "governance", "audits", "templates", "list", env=env
    )

    assert list_result.returncode == 0
    assert "release" in list_result.stdout

    show_result = run_cli(
        "governance", "audits", "templates", "show",
        "--name", "release", env=env,
    )

    assert show_result.returncode == 0
    assert "Name: release" in show_result.stdout

    delete_result = run_cli(
        "governance", "audits", "templates", "delete",
        "--name", "release", env=env,
    )

    assert delete_result.returncode == 0

    show_after_delete = run_cli(
        "governance", "audits", "templates", "show",
        "--name", "release", env=env,
    )

    assert show_after_delete.returncode != 0


def test_templates_create_requires_exactly_one_source(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "templates-source-validation.db")

    result = run_cli(
        "governance", "audits", "templates", "create",
        "--name", "release",
        "--title", "Release Report",
        env=env,
    )

    assert result.returncode != 0
