"""A container that crashes at startup must report its own logs at once, not after the full
poll window and not as "No such container"."""
import subprocess
import time

from backend import cli


def _fake_docker(calls, *, running, exit_code="1", logs="Traceback: boom\n"):
    def run(cmd, **_kwargs):
        calls.append(cmd)
        sub = cmd[1]
        out = ""
        if sub == "run":
            assert "--rm" not in cmd
            out = "abc123\n"
        elif sub == "port":
            out = "127.0.0.1:49999\n"
        elif sub == "inspect":
            out = f"{'true' if running else 'false'} {exit_code}\n"
        elif sub == "logs":
            out = logs
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    return run


def test_crashed_container_reports_logs_and_exit_code_without_waiting(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_docker(calls, running=False))
    monkeypatch.setattr(cli, "DEPLOY_SMOKE_TEST_TIMEOUT_SECONDS", 30.0)
    started = time.monotonic()
    result = cli._run_local_deploy_smoke_test("img:latest", ".")
    assert time.monotonic() - started < 5
    assert result["passed"] is False
    assert "exited with code 1" in result["detail"]
    assert "Traceback: boom" in result["detail"]


def test_container_is_always_removed(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_docker(calls, running=False))
    cli._run_local_deploy_smoke_test("img:latest", ".")
    assert calls[-1][:3] == ["docker", "rm", "-f"]


def test_healthy_container_still_passes(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_docker(calls, running=True))

    class Ok:
        status_code = 200

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Ok())
    assert cli._run_local_deploy_smoke_test("img:latest", ".")["passed"] is True
    assert calls[-1][:3] == ["docker", "rm", "-f"]
