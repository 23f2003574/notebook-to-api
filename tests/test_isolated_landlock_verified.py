"""Landlock isolation is only reported, and only trusted, once every rule is installed and the paths are really denied."""
import os
import sys

import pytest

from backend.runtime import isolated_execution as iso
from tests.test_isolated_storage_hiding import layout, needs_landlock  # noqa: F401 - layout is a fixture

pytestmark = [pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Landlock is Linux-only"), needs_landlock]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(iso, "_HIDDEN", None)


def _marker_func(path):
    def func():
        open(path, "w").write("ran")
        return open(str(path) + ".in").read() if os.path.exists(str(path) + ".in") else "ran"
    return func


@pytest.mark.parametrize("breakage", ["unopenable", "unsupported_rights"])
def test_a_rule_that_cannot_be_installed_stops_required_isolation_before_any_code_runs(layout, monkeypatch, breakage):  # noqa: F811
    real = iso._landlock_rules
    bad = (str(layout / "does-not-exist"), 0x7) if breakage == "unopenable" else (str(layout / "scratch.txt"), 0xFFFF)
    monkeypatch.setattr(iso, "_landlock_rules", lambda protected: real(protected) + [bad])
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_REQUIRE_HIDDEN", "1")
    marker = layout / "ran.txt"
    with pytest.raises(iso.IsolatedExecutionError, match="Landlock rules could not be installed"):
        iso.run_isolated(_marker_func(str(marker)), timeout=30)
    assert not marker.exists()  # the notebook function never started


def test_without_the_requirement_a_partial_ruleset_is_not_applied_and_the_state_says_so(layout, monkeypatch, caplog):  # noqa: F811
    real = iso._landlock_rules
    monkeypatch.setattr(iso, "_landlock_rules", lambda protected: real(protected) + [(str(layout / "gone"), 0x7)])

    def child():
        return iso.hide_protected_paths(), open(str(layout / "data" / "audit.db")).read()

    hidden, audit = iso.run_isolated(child, timeout=30)
    assert hidden == [] and audit == "audit-secret"  # nothing was hidden, and nothing claims otherwise
    monkeypatch.setattr(iso, "_landlock_rules", real)


def test_a_complete_ruleset_hides_the_paths_and_ordinary_files_still_work(layout, monkeypatch):  # noqa: F811
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_REQUIRE_HIDDEN", "1")

    def child():
        denied = False
        try:
            open(str(layout / "data" / "audit.db")).read()
        except PermissionError:
            denied = True
        return denied, open(str(layout / "sub" / "ok.txt")).read(), iso.hide_protected_paths()

    denied, sibling, hidden = iso.run_isolated(child, timeout=30)
    assert denied and sibling == "sibling"
    assert os.path.realpath(layout / "data" / "audit.db") in hidden


def test_a_path_that_stays_readable_after_enforcement_is_refused(layout, monkeypatch):  # noqa: F811
    real_open = os.open
    seen = {}

    def lying_open(path, flags, *a, **k):  # after enforcement the kernel would deny this; make the check see an open
        if str(path).endswith("audit.db") and flags == os.O_RDONLY and seen.get("armed"):
            return real_open("/dev/null", os.O_RDONLY)
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", lying_open)
    seen["armed"] = True
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_REQUIRE_HIDDEN", "1")
    with pytest.raises(iso.IsolatedExecutionError, match="still readable"):
        iso.run_isolated(lambda: "ran", timeout=30)
