"""Isolated children get a file-size cap, an open-file cap and no core dumps."""
import os
import sys

import pytest

from backend.runtime.execution_limits import validate_execution_limits
from backend.runtime.isolated_execution import IsolatedExecutionError, run_isolated

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX rlimits")

KW = dict(timeout_seconds=0, memory_limit_mb=0, cpu_limit_seconds=0, rlimits_supported=True)


def _write(path, size):
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
        fh.flush()
    return os.path.getsize(path)


def _open_many(path, count):
    handles = [open(path) for _ in range(count)]
    return len(handles)


def _core_limit():
    import resource
    return resource.getrlimit(resource.RLIMIT_CORE)


def test_write_past_file_cap_fails_and_within_cap_works(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_MAX_FILE_MB", "1")
    target = str(tmp_path / "f")
    assert run_isolated(_write, args=(target, 512 * 1024), timeout=20) == 512 * 1024
    with pytest.raises(IsolatedExecutionError, match="OSError|File too large"):
        run_isolated(_write, args=(target, 4 * 1024 * 1024), timeout=20)


def test_open_file_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_MAX_OPEN_FILES", "32")
    target = str(tmp_path / "f")
    open(target, "w").close()
    assert run_isolated(_open_many, args=(target, 5), timeout=20) == 5
    with pytest.raises(IsolatedExecutionError, match="OSError|Too many open files"):
        run_isolated(_open_many, args=(target, 200), timeout=20)


def test_core_dumps_are_always_disabled():
    assert run_isolated(_core_limit, timeout=20) == (0, 0)


def test_unset_limits_change_nothing(tmp_path):
    assert run_isolated(_write, args=(str(tmp_path / "f"), 3 * 1024 * 1024), timeout=20) == 3 * 1024 * 1024


@pytest.mark.parametrize("kwargs, message", [
    ({"max_file_mb": -1, "isolate": True}, "finite number"),
    ({"max_file_mb": 5, "isolate": False}, "unless NOTEBOOK_API_ISOLATE_EXECUTION=1"),
    ({"max_open_files": 4, "isolate": True}, "at least|>= 16"),
    ({"max_open_files": 20.5, "isolate": True}, "whole number"),
])
def test_invalid_settings_fail_validation(kwargs, message):
    with pytest.raises(ValueError, match=message):
        validate_execution_limits(**KW, **kwargs)


def test_valid_settings_pass():
    validate_execution_limits(**KW, isolate=True, max_file_mb=10, max_open_files=64)
