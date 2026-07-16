from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


# NOTE: the execution queue has no SQLite persistence (intentionally
# deferred, see deployment_governance_audit_execution_queue.py), so
# every `governance audits queue` invocation is a fresh OS process with
# its own empty in-memory queue -- queue state cannot survive across
# separate CLI invocations. These tests exercise each command's
# self-contained behavior only.


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


def setup_schedule(env: dict[str, str]) -> None:
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
    run_cli(
        "governance", "audits", "schedules", "create",
        "--name", "nightly",
        "--template", "release",
        "--frequency", "daily",
        env=env,
    )


def test_queue_enqueue(tmp_path: Path) -> None:
    env = make_env(tmp_path, "queue-enqueue.db")

    setup_schedule(env)

    result = run_cli(
        "governance", "audits", "queue", "enqueue",
        "--schedule", "nightly", env=env,
    )

    assert result.returncode == 0
    assert "Job queued" in result.stdout
    assert "Schedule: nightly" in result.stdout


def test_queue_enqueue_json(tmp_path: Path) -> None:
    env = make_env(tmp_path, "queue-enqueue-json.db")

    setup_schedule(env)

    result = run_cli(
        "governance", "audits", "queue", "enqueue",
        "--schedule", "nightly", "--json", env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert payload["schedule_name"] == "nightly"
    assert payload["status"] == "pending"


def test_queue_enqueue_rejects_missing_schedule(tmp_path: Path) -> None:
    env = make_env(tmp_path, "queue-enqueue-missing.db")

    result = run_cli(
        "governance", "audits", "queue", "enqueue",
        "--schedule", "missing", env=env,
    )

    assert result.returncode != 0


def test_queue_enqueue_due(tmp_path: Path) -> None:
    env = make_env(tmp_path, "queue-enqueue-due.db")

    setup_schedule(env)

    result = run_cli(
        "governance", "audits", "queue", "enqueue-due", "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(result.stdout)

    assert len(payload) == 1
    assert payload[0]["schedule_name"] == "nightly"


def test_queue_list_empty(tmp_path: Path) -> None:
    env = make_env(tmp_path, "queue-list-empty.db")

    result = run_cli("governance", "audits", "queue", "list", env=env)

    assert result.returncode == 0
    assert "Execution Queue" in result.stdout


def test_queue_show_missing(tmp_path: Path) -> None:
    env = make_env(tmp_path, "queue-show-missing.db")

    result = run_cli(
        "governance", "audits", "queue", "show",
        "--job-id", "missing", env=env,
    )

    assert result.returncode != 0


def test_queue_delete_missing(tmp_path: Path) -> None:
    env = make_env(tmp_path, "queue-delete-missing.db")

    result = run_cli(
        "governance", "audits", "queue", "delete",
        "--job-id", "missing", env=env,
    )

    assert result.returncode != 0


def test_queue_clear(tmp_path: Path) -> None:
    env = make_env(tmp_path, "queue-clear.db")

    result = run_cli("governance", "audits", "queue", "clear", env=env)

    assert result.returncode == 0
    assert "cleared" in result.stdout
