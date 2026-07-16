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


def test_labels_add_and_show(tmp_path: Path) -> None:
    env = make_env(tmp_path, "labels-add-show.db")

    run_cli("governance", "check", env=env)
    audit_id = latest_audit_id(env)

    add_result = run_cli(
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

    assert add_result.returncode == 0
    assert "Label added" in add_result.stdout

    show_result = run_cli(
        "governance",
        "audits",
        "labels",
        "show",
        "--audit-id",
        audit_id,
        env=env,
    )

    assert show_result.returncode == 0
    assert "release" in show_result.stdout


def test_labels_search(tmp_path: Path) -> None:
    env = make_env(tmp_path, "labels-search.db")

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
        "labels",
        "search",
        "--label",
        "release",
        env=env,
    )

    assert result.returncode == 0
    assert audit_id in result.stdout


def test_labels_remove(tmp_path: Path) -> None:
    env = make_env(tmp_path, "labels-remove.db")

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

    remove_result = run_cli(
        "governance",
        "audits",
        "labels",
        "remove",
        "--audit-id",
        audit_id,
        "--label",
        "release",
        env=env,
    )

    assert remove_result.returncode == 0

    show_result = run_cli(
        "governance",
        "audits",
        "labels",
        "show",
        "--audit-id",
        audit_id,
        env=env,
    )

    assert "No labels" in show_result.stdout


def test_labels_list_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "labels-list.db")

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
        "governance", "audits", "labels", "list", "--json", env=env
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert isinstance(payload, list)
    assert payload[0]["audit_id"] == audit_id


def test_labels_add_rejects_missing_audit(tmp_path: Path) -> None:
    env = make_env(tmp_path, "labels-missing.db")

    result = run_cli(
        "governance",
        "audits",
        "labels",
        "add",
        "--audit-id",
        "missing",
        "--label",
        "release",
        env=env,
    )

    assert result.returncode != 0
