"""Ratchet for unused imports (F401), unchained re-raises (B904) and the S3xx security rules.

The project lint (pyproject.toml) enforces bug-finding rules only, because these three groups
already have hundreds of findings in existing code. tests/lint_ratchet_baseline.json records how many
each backend file has today. New code may not add any (a count above the baseline fails), and the
baseline may only shrink (a count below it fails until you lower it), so the debt is paid down and
never re-grows. After fixing violations, lower the baseline with:

    python tests/test_lint_ratchet.py --update

--update only ever lowers counts; it refuses to record an increase. The S3xx findings in the baseline
were reviewed by hand: the remaining ones are suppressed in the source with a reason, not baselined."""
import collections
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).with_name("lint_ratchet_baseline.json")
RULES = "F401,B904,S3"


def _ruff(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format", "json", *args],
                          cwd=cwd, capture_output=True, text=True, timeout=300)


def current_counts():
    """{"path::RULE": count} for the backend, with the project's own pyproject rules isolated away."""
    result = _ruff("backend", "--isolated", "--select", RULES)
    counts = collections.Counter()
    for item in json.loads(result.stdout or "[]"):
        path = Path(item["filename"]).resolve().relative_to(ROOT).as_posix()
        counts[f"{path}::{item['code']}"] += 1
    return dict(counts)


def compare(baseline, actual):
    """(increases, decreases): keys whose count is above / below the baseline."""
    keys = set(baseline) | set(actual)
    increases = sorted(k for k in keys if actual.get(k, 0) > baseline.get(k, 0))
    decreases = sorted(k for k in keys if actual.get(k, 0) < baseline.get(k, 0))
    return increases, decreases


def test_no_new_unused_imports_unchained_reraises_or_insecure_calls():
    baseline = json.loads(BASELINE.read_text())
    increases, _ = compare(baseline, current_counts())
    assert not increases, "new lint violations (fix them; do not raise the baseline): " + ", ".join(increases[:20])


def test_baseline_only_shrinks():
    baseline = json.loads(BASELINE.read_text())
    _, decreases = compare(baseline, current_counts())
    assert not decreases, "fewer violations than the baseline records; run `python tests/test_lint_ratchet.py --update`: " + ", ".join(decreases[:20])


def test_rules_catch_representative_violations(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import os\nimport hashlib\nimport pickle\n\n"
        "def f(x):\n    try:\n        return int(x)\n    except ValueError:\n        raise RuntimeError('bad')\n\n"
        "def g(data):\n    return hashlib.md5(data).hexdigest(), pickle.loads(data)\n")
    codes = {item["code"] for item in json.loads(_ruff(str(sample), "--isolated", "--select", RULES, cwd=tmp_path).stdout)}
    assert codes == {"F401", "B904", "S324", "S301"}


def test_ratchet_comparison_detects_growth_and_shrinkage():
    base = {"a.py::F401": 2, "b.py::B904": 1}
    assert compare(base, {"a.py::F401": 3, "b.py::B904": 1}) == (["a.py::F401"], [])
    assert compare(base, {"a.py::F401": 2, "c.py::S301": 1}) == (["c.py::S301"], ["b.py::B904"])
    assert compare(base, base) == ([], [])


def test_the_security_rules_are_part_of_the_ratchet():
    assert "S3" in RULES and "F401" in RULES and "B904" in RULES


if __name__ == "__main__":
    actual = current_counts()
    if "--update" not in sys.argv:
        print(json.dumps(actual, indent=1, sort_keys=True))
        sys.exit(0)
    old = json.loads(BASELINE.read_text()) if BASELINE.exists() else None
    if old is not None:
        increases, _ = compare(old, actual)
        if increases:
            sys.exit("refusing to raise the baseline; fix these first: " + ", ".join(increases[:20]))
        actual = {k: min(v, old.get(k, v)) for k, v in actual.items()}
    BASELINE.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n")
    print(f"baseline written: {sum(actual.values())} violations in {len(actual)} entries")
