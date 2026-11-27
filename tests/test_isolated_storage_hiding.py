"""Notebook children must not reach the audit database or other service-owned paths."""
import sys

import pytest

from backend.runtime import isolated_execution as iso


def _read(path):
    try:
        with open(path, "rb") as handle:
            return handle.read().decode()
    except PermissionError:
        return "DENIED"


def _landlock():
    import ctypes
    return ctypes.CDLL(None).syscall(444, None, 0, 1) >= 1


needs_landlock = pytest.mark.skipif(not (sys.platform.startswith("linux") and _landlock()), reason="Landlock unavailable")


@pytest.fixture
def layout(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "audit.db").write_text("audit-secret")
    (data / "audit.db-wal").write_text("wal-secret")
    (tmp_path / "scratch.txt").write_text("notebook-input")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "ok.txt").write_text("sibling")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(data / "audit.db"))
    for name in ("NOTEBOOK_API_ISOLATE_HIDE_PATHS", "NOTEBOOK_API_ISOLATE_REQUIRE_HIDDEN", "NOTEBOOK_API_ISOLATE_HIDE_STORAGE"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_protected_paths_cover_the_database_sidecars_and_explicit_paths(layout, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_HIDE_PATHS", str(layout / "sub"))
    names = [p.rsplit("/", 1)[-1] for p in iso.protected_paths()]
    assert names == ["sub", "audit.db", "audit.db-wal"]


@needs_landlock
def test_child_cannot_read_audit_database_but_can_read_everything_else(layout):
    result = iso.run_isolated(lambda: [
        _read(layout / "data" / "audit.db"), _read(layout / "data" / "audit.db-wal"),
        _read(layout / "scratch.txt"), _read(layout / "sub" / "ok.txt"),
    ], timeout=30)
    assert result == ["DENIED", "DENIED", "notebook-input", "sibling"]


@needs_landlock
def test_hidden_storage_directories_and_cleanup_of_the_parent(layout, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_HIDE_PATHS", str(layout / "sub"))
    assert iso.run_isolated(lambda: _read(layout / "sub" / "ok.txt"), timeout=30) == "DENIED"
    assert (layout / "sub" / "ok.txt").read_text() == "sibling"  # the service itself is unaffected


def _without_landlock(monkeypatch):
    import ctypes

    monkeypatch.setattr(sys, "platform", "linux")
    real = ctypes.CDLL(None)

    class NoLandlock:
        def syscall(self, *args):
            return -1
        prctl = real.prctl

    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: NoLandlock())


def test_without_landlock_it_warns_and_runs_unless_hiding_is_required(layout, monkeypatch, caplog):
    _without_landlock(monkeypatch)
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        assert iso.hide_protected_paths() == []
    assert "storage_not_hidden" in caplog.text


def test_fails_closed_when_hiding_is_required_and_the_kernel_cannot_do_it(layout, monkeypatch):
    _without_landlock(monkeypatch)
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_REQUIRE_HIDDEN", "1")
    with pytest.raises(iso.IsolatedExecutionError, match="REQUIRE_HIDDEN"):
        iso.hide_protected_paths()


def test_nothing_to_hide_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", raising=False)
    assert iso.hide_protected_paths() == []
