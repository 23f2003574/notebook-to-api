"""Regression: the root requirements.txt must not list unused look-alike
packages (e.g. ``httpx2``), which nothing imports and which is a
dependency-confusion risk for anyone running ``pip install -r``."""
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"


def _names():
    return [
        line.split("==")[0].split(">=")[0].strip().lower()
        for line in REQUIREMENTS.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_no_unused_httpx2_requirement():
    assert "httpx2" not in _names()


def test_httpx_still_required_for_testclient():
    assert "httpx" in _names()


def test_no_duplicate_requirements():
    names = _names()
    assert len(names) == len(set(names))
