from __future__ import annotations

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


def test_verify_command_passes_for_valid_export(tmp_path: Path) -> None:
    env = make_env(tmp_path, "verify-valid.db")
    output_path = tmp_path / "evidence.json"

    run_cli(
        "governance",
        "audits",
        "export",
        "--output",
        str(output_path),
        env=env,
    )

    result = run_cli(
        "governance",
        "audits",
        "verify",
        "--evidence",
        str(output_path),
        env=env,
    )

    assert result.returncode == 0
    assert "Status: VERIFIED" in result.stdout


def test_verify_command_fails_for_tampered_evidence(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "verify-tampered.db")
    output_path = tmp_path / "evidence.json"

    run_cli(
        "governance",
        "audits",
        "export",
        "--output",
        str(output_path),
        env=env,
    )

    output_path.write_text(
        output_path.read_text(encoding="utf-8") + " ",
        encoding="utf-8",
    )

    result = run_cli(
        "governance",
        "audits",
        "verify",
        "--evidence",
        str(output_path),
        env=env,
    )

    assert result.returncode == 3
    assert "verification failed" in result.stdout.lower()


def test_verify_command_accepts_explicit_manifest_path(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "verify-explicit-manifest.db")
    output_path = tmp_path / "evidence.json"
    manifest_path = tmp_path / "evidence.json.manifest.json"

    run_cli(
        "governance",
        "audits",
        "export",
        "--output",
        str(output_path),
        env=env,
    )

    result = run_cli(
        "governance",
        "audits",
        "verify",
        "--evidence",
        str(output_path),
        "--manifest",
        str(manifest_path),
        env=env,
    )

    assert result.returncode == 0


def test_verify_command_requires_evidence_argument() -> None:
    result = run_cli("governance", "audits", "verify")

    assert result.returncode != 0


def test_verify_command_reports_missing_manifest_as_exit_two(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "orphan-evidence.json"
    evidence_path.write_text("{}", encoding="utf-8")

    result = run_cli(
        "governance",
        "audits",
        "verify",
        "--evidence",
        str(evidence_path),
    )

    assert result.returncode == 2
