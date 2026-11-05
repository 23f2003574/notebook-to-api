"""`deploy --push` with a --tag that names no registry/namespace must be
rejected before any compile or build (docker push would be denied)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK = ROOT / "notebooks" / "sample.ipynb"


def _deploy(tmp_path, *extra):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-m", "backend.cli", "deploy", str(NOTEBOOK),
         "--output", str(tmp_path / "built_api"), "--dry-run", *extra],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60,
    )


def test_push_with_registryless_tag_is_rejected_before_building(tmp_path):
    proc = _deploy(tmp_path, "--push", "--tag", "plain:1")
    assert proc.returncode != 0
    assert "registry" in proc.stderr + proc.stdout
    assert not (tmp_path / "built_api").exists()


def test_push_with_registry_tag_is_accepted(tmp_path):
    proc = _deploy(tmp_path, "--push", "--tag", "registry.example.com/myapp:v1")
    assert proc.returncode == 0, proc.stderr
    assert "Would push" in proc.stdout


def test_registryless_tag_without_push_is_still_accepted(tmp_path):
    assert _deploy(tmp_path, "--tag", "plain:1").returncode == 0
