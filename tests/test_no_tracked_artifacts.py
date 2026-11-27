"""Build and test artifacts never belong in git (a tracked .coverage is rewritten or deleted by every test run)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_NAMES = (".coverage", "coverage.xml")


def _tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return out.stdout.splitlines() if out.returncode == 0 else None


def test_no_coverage_database_or_build_artifact_is_tracked():
    tracked = _tracked()
    if tracked is None:
        return  # not a git checkout (an sdist or archive)
    bad = [p for p in tracked if Path(p).name in ARTIFACT_NAMES or Path(p).name.startswith(".coverage.")
           or "__pycache__" in p or p.endswith((".pyc", ".pyo")) or p.startswith((".ruff_cache/", "htmlcov/"))]
    assert not bad, f"untrack these (git rm --cached): {bad}"


def test_gitignore_covers_every_artifact_the_test_run_writes():
    ignored = (ROOT / ".gitignore").read_text().split()
    for pattern in (".coverage", ".coverage.*", "coverage.xml", "htmlcov/", ".ruff_cache/", "__pycache__/"):
        assert pattern in ignored, pattern


def test_git_agrees_the_artifacts_are_ignored(tmp_path):
    if _tracked() is None:
        return
    for name in (".coverage", "coverage.xml", ".coverage.abc"):
        checked = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT)
        assert checked.returncode == 0, f"{name} is not ignored"
