from __future__ import annotations

import json
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


def test_governance_doctor_cli_command_defaults_to_memory_backend(
    monkeypatch,
) -> None:
    monkeypatch.delenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND",
        raising=False,
    )

    result = run_cli(
        "governance",
        "doctor",
    )

    assert result.returncode == 0

    assert (
        "Deployment Governance Persistence Doctor"
        in result.stdout
    )

    assert (
        "Status: HEALTHY"
        in result.stdout
    )

    assert (
        "Backend: memory"
        in result.stdout
    )


def test_governance_doctor_cli_json_output_is_clean_stdout() -> None:
    result = run_cli(
        "governance",
        "doctor",
        "--json",
    )

    assert result.returncode == 0

    payload = json.loads(
        result.stdout
    )

    assert "backend" in payload

    assert (
        "operationally_healthy"
        in payload
    )

    assert result.stderr == ""


def test_governance_doctor_cli_sqlite_deep_json(
    tmp_path: Path,
) -> None:
    import os

    database_path = (
        tmp_path
        / "cli-doctor.db"
    )

    env = dict(
        os.environ
    )

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(
        database_path
    )

    result = run_cli(
        "governance",
        "doctor",
        "--deep",
        "--json",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(
        result.stdout
    )

    assert (
        payload["backend"]
        == "sqlite"
    )

    assert (
        payload["schema"]["current_version"]
        == 22
    )

    assert (
        payload["integrity"]["executed"]
        is True
    )

    assert (
        payload["integrity_verified"]
        is True
    )

    assert (
        payload["audit_history"]["current_audit_recorded"]
        is True
    )

    assert (
        payload["audit_history"]["total_audits"]
        == 1
    )


def test_governance_doctor_cli_accepts_batch_size(
    tmp_path: Path,
) -> None:
    import os

    env = dict(
        os.environ
    )

    env["NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND"] = "sqlite"
    env["NOTEBOOK2API_GOVERNANCE_DATABASE_PATH"] = str(
        tmp_path
        / "cli-batch.db"
    )

    result = run_cli(
        "governance",
        "doctor",
        "--deep",
        "--batch-size",
        "1",
        env=env,
    )

    assert result.returncode == 0

    assert (
        "Integrity status: HEALTHY"
        in result.stdout
    )


def test_governance_doctor_cli_rejects_invalid_batch_size() -> None:
    result = run_cli(
        "governance",
        "doctor",
        "--batch-size",
        "0",
    )

    assert result.returncode != 0
