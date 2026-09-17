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


def setup_template(env: dict[str, str]) -> None:
    run_cli(
        "governance", "audits", "collections", "create",
        "--name", "release-v1", env=env,
    )
    run_cli(
        "governance", "audits", "templates", "create",
        "--name", "release",
        "--title", "Release Report",
        "--collection", "release-v1",
        env=env,
    )


def test_schedules_full_lifecycle(tmp_path: Path) -> None:
    env = make_env(tmp_path, "schedules-lifecycle.db")

    setup_template(env)

    create_result = run_cli(
        "governance", "audits", "schedules", "create",
        "--name", "nightly",
        "--template", "release",
        "--frequency", "daily",
        env=env,
    )

    assert create_result.returncode == 0
    assert "Schedule created" in create_result.stdout

    list_result = run_cli(
        "governance", "audits", "schedules", "list", env=env
    )

    assert list_result.returncode == 0
    assert "nightly" in list_result.stdout

    show_result = run_cli(
        "governance", "audits", "schedules", "show",
        "--name", "nightly", env=env,
    )

    assert show_result.returncode == 0
    assert "Status: enabled" in show_result.stdout

    disable_result = run_cli(
        "governance", "audits", "schedules", "disable",
        "--name", "nightly", env=env,
    )

    assert disable_result.returncode == 0

    show_after_disable = run_cli(
        "governance", "audits", "schedules", "show",
        "--name", "nightly", env=env,
    )

    assert "Status: disabled" in show_after_disable.stdout

    enable_result = run_cli(
        "governance", "audits", "schedules", "enable",
        "--name", "nightly", env=env,
    )

    assert enable_result.returncode == 0

    delete_result = run_cli(
        "governance", "audits", "schedules", "delete",
        "--name", "nightly", env=env,
    )

    assert delete_result.returncode == 0

    show_after_delete = run_cli(
        "governance", "audits", "schedules", "show",
        "--name", "nightly", env=env,
    )

    assert show_after_delete.returncode != 0


def test_schedules_create_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "schedules-json.db")

    setup_template(env)

    result = run_cli(
        "governance", "audits", "schedules", "create",
        "--name", "nightly",
        "--template", "release",
        "--frequency", "daily",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["name"] == "nightly"
    assert payload["template_name"] == "release"
    assert payload["frequency"] == "daily"
    assert payload["enabled"] is True


def test_schedules_create_rejects_missing_template(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "schedules-missing-template.db")

    result = run_cli(
        "governance", "audits", "schedules", "create",
        "--name", "nightly",
        "--template", "missing",
        "--frequency", "daily",
        env=env,
    )

    assert result.returncode != 0
