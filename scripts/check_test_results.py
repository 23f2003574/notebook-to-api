"""Summarise test results and fail on anything that is not a clean first-attempt pass.

Usage: python scripts/check_test_results.py junit  <junit.xml> [more ...]
       python scripts/check_test_results.py playwright <results.json>

Policy: a failing test fails the run, and so does a test that only passed after a retry (JUnit
<flakyFailure>/<rerunFailure>, Playwright status "flaky"): a retry is kept for its trace, but it
must not turn a real flake green. A missing, unparsable or empty report also fails, so a job
cannot pass by not producing one. The summary goes to stdout and to $GITHUB_STEP_SUMMARY."""
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def junit(paths):
    total = skipped = 0
    failed, retried = [], []
    for path in paths:
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            total += 1
            if case.find("skipped") is not None:
                skipped += 1
            elif case.find("failure") is not None or case.find("error") is not None:
                failed.append(name)
            elif case.find("flakyFailure") is not None or case.find("rerunFailure") is not None:
                retried.append(name)
    return total, skipped, failed, retried


def playwright(path):
    report = json.loads(Path(path).read_text())
    counts = {"total": 0, "skipped": 0}
    failed, retried = [], []

    def walk(suite, trail):
        for child in suite.get("suites", []):
            walk(child, trail + [child.get("title", "")])
        for spec in suite.get("specs", []):
            name = " > ".join(t for t in trail + [spec.get("title", "")] if t)
            for test in spec.get("tests", []):
                counts["total"] += 1
                status = test.get("status")
                if status == "skipped":
                    counts["skipped"] += 1
                elif status == "unexpected":
                    failed.append(name)
                elif status == "flaky":
                    retried.append(name)

    for suite in report.get("suites", []):
        walk(suite, [suite.get("title", "")])
    return counts["total"], counts["skipped"], failed, retried


def main(argv):
    kind, paths = argv[1], argv[2:]
    try:
        missing = [p for p in paths if not Path(p).is_file()]
        if missing or not paths:
            raise FileNotFoundError(f"no test report at {', '.join(missing) or '(no path given)'}")
        total, skipped, failed, retried = junit(paths) if kind == "junit" else playwright(paths[0])
        if total == 0:
            raise ValueError("the report contains no tests")
    except (OSError, ValueError, ET.ParseError, KeyError) as exc:
        print(f"test results: unusable report: {exc}")
        return 1
    lines = [f"### Test results ({kind})", f"{total} tests, {skipped} skipped, {len(failed)} failed, {len(retried)} passed only after a retry"]
    lines += [f"- FAILED: {name}" for name in failed] + [f"- PASSED ONLY ON RETRY: {name}" for name in retried]
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 1 if failed or retried else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
