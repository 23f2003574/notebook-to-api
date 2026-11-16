"""The compile smoke test imports uploaded notebook code; it must run in a
bounded child process so that code cannot hang or kill the dashboard."""
import time

import pytest

from backend.routes import upload as upload_module

HEALTHY = (
    "from fastapi import FastAPI\n"
    "app = FastAPI()\n"
    "@app.get('/health')\n"
    "def health():\n    return {'ok': True}\n"
)


@pytest.fixture
def smoke(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    counter = iter(range(1000))

    def run(top_level, timeout=None):
        name = f"smoke_pkg_{next(counter)}"
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "app.py").write_text(top_level + HEALTHY)
        if timeout is not None:
            monkeypatch.setattr(upload_module, "SMOKE_TEST_TIMEOUT_SECONDS", timeout)
        return upload_module._run_compile_smoke_test(name)

    return run


def test_healthy_app_passes(smoke):
    assert smoke("") == {"passed": True, "status_code": 200, "detail": None}


def test_a_hanging_import_times_out_instead_of_blocking(smoke):
    started = time.monotonic()
    result = smoke("import time\ntime.sleep(60)\n", timeout=2)
    assert time.monotonic() - started < 20
    assert result["passed"] is False and "did not answer" in result["detail"]


def test_os_exit_at_import_does_not_kill_the_caller(smoke):
    result = smoke("import os\nos._exit(3)\n")
    assert result["passed"] is False and "status 3" in result["detail"]


def test_sys_exit_and_import_errors_are_reported(smoke):
    assert "sys.exit(2)" in smoke("import sys\nsys.exit(2)\n")["detail"]
    assert "ZeroDivisionError" in smoke("1/0\n")["detail"]
