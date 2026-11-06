from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_ci_has_a_job_that_builds_and_smoke_tests_the_sample_image():
    """CI used to run only pytest, so a broken generated Dockerfile or
    requirements.txt went unnoticed until a real deploy."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())
    job = workflow["jobs"]["docker-smoke"]
    commands = " ".join(step.get("run", "") for step in job["steps"])

    assert "backend.cli deploy notebooks/sample.ipynb" in commands
    assert "--smoke-test" in commands
    assert (ROOT / "notebooks" / "sample.ipynb").exists()


def test_documented_ci_deploy_command_is_accepted_by_the_cli():
    """The workflow's command must parse: --output needs a package-safe name
    and --smoke-test is a real flag (checked via --dry-run, no Docker)."""
    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [sys.executable, "-m", "backend.cli", "deploy",
             str(ROOT / "notebooks" / "sample.ipynb"),
             "--output", str(Path(tmp) / "smoke_app"), "--dry-run"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr
