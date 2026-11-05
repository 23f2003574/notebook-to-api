"""`deploy --platform` must start with a real OS: a bare architecture
(`arm64`) or a typo (`bogus`) is rejected before the compile and build."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK = ROOT / "notebooks" / "sample.ipynb"


def _deploy(tmp_path, platform):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-m", "backend.cli", "deploy", str(NOTEBOOK),
         "--output", str(tmp_path / "built_api"), "--dry-run",
         "--platform", platform],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("platform", ["arm64", "amd64", "bogus", "x86/amd64"])
def test_platform_without_a_real_os_is_rejected_before_building(tmp_path, platform):
    proc = _deploy(tmp_path, platform)
    assert proc.returncode != 0
    assert "--platform" in proc.stderr + proc.stdout
    assert not (tmp_path / "built_api").exists()


@pytest.mark.parametrize("platform", ["linux/amd64", "linux/arm64/v8", "linux/amd64,linux/arm64"])
def test_valid_platforms_are_still_accepted(tmp_path, platform):
    assert _deploy(tmp_path, platform).returncode == 0
