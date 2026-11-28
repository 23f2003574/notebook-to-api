"""Fail when a security-critical module's coverage is below its recorded floor (coverage_floors.json).

Usage: python scripts/check_coverage_floors.py coverage.json   (the output of `coverage json`)

coverage.py's own fail_under is one number for the whole selection, so one well-tested module hides
another's regression. Every file in the floors file must appear in the report (a module that silently
dropped out of the measurement fails too) and reach its floor. Raise a floor when coverage rises; never
lower one to make a build pass."""
import json
import sys
from pathlib import Path

FLOORS = Path(__file__).resolve().parent.parent / "coverage_floors.json"


def check(report: dict, floors: dict) -> list[str]:
    files = report.get("files", {})
    problems = []
    for path, floor in sorted(floors.items()):
        if path not in files:
            problems.append(f"{path}: missing from the coverage report (not measured)")
            continue
        percent = files[path]["summary"]["percent_covered"]
        if percent + 1e-9 < floor:
            problems.append(f"{path}: {percent:.2f}% is below its floor of {floor}%")
    return problems


def main(argv: list[str]) -> int:
    report = json.loads(Path(argv[1]).read_text())
    problems = check(report, json.loads(FLOORS.read_text()))
    for problem in problems:
        print("coverage floor:", problem)
    if not problems:
        print(f"coverage floors: all {len(json.loads(FLOORS.read_text()))} modules at or above their floor")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
