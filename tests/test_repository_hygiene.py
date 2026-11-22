"""Nothing but the sample notebook may be tracked under uploads/ (test runs write scratch files there)."""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_only_the_sample_notebook_is_tracked_in_uploads():
    tracked = subprocess.run(["git", "ls-files", "uploads"], cwd=REPO, capture_output=True, text=True)
    if tracked.returncode != 0:
        return  # not a git checkout (an sdist or wheel build)
    assert set(tracked.stdout.split()) <= {"uploads/sample.ipynb"}


def test_scratch_files_in_uploads_are_ignored_by_git():
    ignored = subprocess.run(
        ["git", "check-ignore", "uploads/.compile_history.json", "uploads/scratch.ipynb"], cwd=REPO, capture_output=True, text=True,
    )
    if ignored.returncode not in (0, 1):
        return
    assert ignored.stdout.split() == ["uploads/.compile_history.json", "uploads/scratch.ipynb"]
    assert subprocess.run(["git", "check-ignore", "uploads/sample.ipynb"], cwd=REPO, capture_output=True).returncode == 1
