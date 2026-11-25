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


def test_smoke_container_runs_with_the_compose_restrictions():
    from backend.generator.docker_generator import docker_compose_content, smoke_test_run_command

    command = smoke_test_run_command("img:latest")
    assert command[-1] == "img:latest" and "--rm" not in command
    for flag in ("--read-only", "--tmpfs", "--cap-drop", "--pids-limit"):
        assert flag in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges:true" in command and "NOTEBOOK_API_ALLOW_DEFAULT_KEY=true" in command
    compose = docker_compose_content("svc", [])
    assert 'cap_drop: ["ALL"]' in compose and "read_only:" in compose and "no-new-privileges:true" in compose


def test_dashboard_smoke_test_reports_a_crash_at_once(monkeypatch):
    from backend.routes import upload as upload_module

    calls = []
    monkeypatch.setattr(upload_module.subprocess, "run", _fake_docker(calls, running=False))
    monkeypatch.setattr(upload_module, "DEPLOY_SMOKE_TEST_TIMEOUT_SECONDS", 30.0)
    started = time.monotonic()
    result = upload_module._run_deploy_smoke_test("img:latest", ".")
    assert time.monotonic() - started < 5
    assert result["passed"] is False and "exited with code 1" in result["detail"] and "Traceback: boom" in result["detail"]
    assert calls[-1][:3] == ["docker", "rm", "-f"]
