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


def test_report_collection_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "report-collection.db")

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

    result = run_cli(
        "governance", "audits", "report", "collection",
        "--collection", "release-v1", env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["title"] == "release-v1"
    assert payload["audits"][0]["audit_id"] == audit_id


def test_report_collection_markdown(tmp_path: Path) -> None:
    env = make_env(tmp_path, "report-collection-md.db")

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

    result = run_cli(
        "governance", "audits", "report", "collection",
        "--collection", "release-v1", "--format", "md", env=env,
    )

    assert result.returncode == 0
    assert "# release-v1" in result.stdout
    assert "## Statistics" in result.stdout


def test_report_audits_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "report-audits.db")

    run_cli("governance", "check", env=env)
    run_cli("governance", "check", env=env)

    replays = json.loads(
        run_cli(
            "governance", "audits", "replay", "--limit", "2", "--json",
            env=env,
        ).stdout
    )

    audit_ids = [entry["audit_id"] for entry in replays]

    result = run_cli(
        "governance", "audits", "report", "audits",
        "--audit-id", audit_ids[0],
        "--audit-id", audit_ids[1],
        "--title", "Selected Audits",
        "--format", "json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["title"] == "Selected Audits"
    assert [a["audit_id"] for a in payload["audits"]] == audit_ids


def test_report_audits_requires_at_least_one_audit_id(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "report-audits-none.db")

    result = run_cli(
        "governance", "audits", "report", "audits",
        "--title", "Empty", env=env,
    )

    assert result.returncode != 0
