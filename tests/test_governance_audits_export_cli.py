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


def test_governance_audit_export_writes_evidence_file(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "export-basic.db")
    output_path = tmp_path / "evidence.json"

    result = run_cli(
        "governance",
        "audits",
        "export",
        "--output",
        str(output_path),
        env=env,
    )

    assert result.returncode == 0
    assert output_path.exists()

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1


def test_governance_audit_export_requires_force_to_overwrite(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "export-overwrite.db")
    output_path = tmp_path / "evidence.json"
    output_path.write_text("{}", encoding="utf-8")

    result = run_cli(
        "governance",
        "audits",
        "export",
        "--output",
        str(output_path),
        env=env,
    )

    assert result.returncode != 0

    forced = run_cli(
        "governance",
        "audits",
        "export",
        "--output",
        str(output_path),
        "--force",
        env=env,
    )

    assert forced.returncode == 0

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1


def test_governance_audit_export_requires_output_argument() -> None:
    result = run_cli("governance", "audits", "export")

    assert result.returncode != 0


def test_governance_audit_export_respects_limit_and_no_analysis_flags(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "export-limit.db")

    for _ in range(3):
        run_cli("governance", "check", env=env)

    output_path = tmp_path / "evidence.json"

    result = run_cli(
        "governance",
        "audits",
        "export",
        "--output",
        str(output_path),
        "--limit",
        "1",
        "--no-trend",
        "--no-regression",
        env=env,
    )

    assert result.returncode == 0

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["record_count"] == 1
    assert payload["trend"] is None
    assert payload["regression"] is None


def test_governance_audit_export_compact_output_is_single_line(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "export-compact.db")
    output_path = tmp_path / "evidence.json"

    result = run_cli(
        "governance",
        "audits",
        "export",
        "--output",
        str(output_path),
        "--compact",
        env=env,
    )

    assert result.returncode == 0

    content = output_path.read_text(encoding="utf-8")

    assert content.count("\n") == 1  # trailing newline only
    assert json.loads(content)["schema_version"] == 1


def test_governance_audit_export_does_not_dump_bundle_to_stdout(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path, "export-stdout.db")
    output_path = tmp_path / "evidence.json"

    result = run_cli(
        "governance",
        "audits",
        "export",
        "--output",
        str(output_path),
        env=env,
    )

    assert result.returncode == 0
    assert "record_count" not in result.stdout
    assert "Records exported:" in result.stdout
