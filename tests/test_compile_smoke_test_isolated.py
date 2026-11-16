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


def _alive(pid):
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_dead(pid, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.05)
    return not _alive(pid)


def test_timeout_kills_the_whole_process_group_including_grandchildren(smoke, tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    result = smoke(
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(120)\n",
        timeout=3,
    )
    assert result["passed"] is False and "did not answer" in result["detail"]
    assert _wait_dead(int(pidfile.read_text()))


def test_a_child_ignoring_sigterm_is_killed_and_reaped(smoke, tmp_path):
    pidfile = tmp_path / "child.pid"
    started = time.monotonic()
    result = smoke(
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(120)\n",
        timeout=3,
    )
    assert result["passed"] is False
    assert time.monotonic() - started < 30
    assert _wait_dead(int(pidfile.read_text()))  # reaped: not even a zombie answers kill(pid, 0)


def test_child_environment_excludes_secrets(smoke, tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "dash-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("HARMLESS_SETTING", "visible")
    seen = tmp_path / "env.txt"
    smoke(f"import os\nopen({str(seen)!r}, 'w').write(repr(dict(os.environ)))\n")
    text = seen.read_text()
    assert "dash-secret" not in text and "aws-secret" not in text and "visible" in text


def test_concurrent_smoke_tests_stay_isolated(smoke):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda i: smoke("" if i % 2 == 0 else "import sys\nsys.exit(1)\n"), range(4)))
    assert [r["passed"] for r in results] == [True, False, True, False]



def test_a_leaked_backend_directory_on_sys_path_cannot_shadow_the_child_imports(smoke, monkeypatch):
    """backend/ holds packages named platform, security, ... that would replace the standard
    library in the child if the parent's sys.path were forwarded (it broke pydantic on 3.11)."""
    import pathlib
    monkeypatch.syspath_prepend(str(pathlib.Path(upload_module.__file__).resolve().parents[1]))
    assert smoke("import platform\n")["passed"] is True
