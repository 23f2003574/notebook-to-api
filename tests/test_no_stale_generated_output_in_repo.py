"""Compiled output must never be committed: a stale `test_generated/` app
(importing a runtime module that was not even in the repo) used to ship in it."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_generated_output_directories_are_gitignored_and_not_tracked():
    ignore = (ROOT / ".gitignore").read_text().splitlines()
    assert "generated/" in ignore and "test_generated/" in ignore
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True,
    ).stdout.splitlines()
    stale = [f for f in tracked if f.split("/")[0] in ("generated", "test_generated")]
    assert stale == []
