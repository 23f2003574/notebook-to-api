import json
import os
import stat
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_notebook(path):
    path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _install_fake_docker(bin_dir, log_path):
    """A fake `docker` executable that records how it was invoked instead of
    actually building an image, so these tests don't need a real Docker
    daemon (mirrors the fake `requests` module used to test the generated
    SDK client in test_sdk_generator.py).
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$@" > "{log_path}"\n'
        f'pwd >> "{log_path}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _install_fake_docker_recording_all_calls(bin_dir, log_path):
    """Like _install_fake_docker, but appends a record per invocation
    instead of overwriting, separated by a marker line -- so a test
    exercising multiple docker calls in one run (build followed by push)
    can inspect each call independently, in the order they happened.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/bin/sh\n"
        f'{{ printf \'%s\\n\' "$@"; pwd; printf \'%s\\n\' "==CALL=="; }} >> "{log_path}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_cli(args, cwd, path_dirs=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    if path_dirs:
        env["PATH"] = os.pathsep.join([*path_dirs, env.get("PATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "backend.cli", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_deploy_command_is_registered(tmp_path):
    """Confirmed exploitable before this fix: `deploy` had a real dispatch
    branch in main() but no matching subparsers.add_parser("deploy", ...),
    so argparse rejected it outright with "invalid choice: 'deploy'"
    before the branch was ever reached.
    """

    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", "deploy", "--help"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "invalid choice" not in proc.stderr
    assert "notebook" in proc.stdout


def test_deploy_compiles_and_builds_with_default_tag(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])

    proc = subprocess.run(
        [
            sys.executable, "-m", "backend.cli", "deploy",
            str(notebook_path), "--output", "built_api",
        ],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    # The notebook must actually have been compiled before the image build.
    assert (workdir / "built_api" / "app.py").exists()
    assert (workdir / "built_api" / "Dockerfile").exists()

    log_lines = log_path.read_text(encoding="utf-8").splitlines()
    assert log_lines[:-1] == ["build", "-t", "built_api:latest", "."]
    # docker build must run with the compiled output dir as its context.
    assert log_lines[-1] == str((workdir / "built_api").resolve())

    assert "Docker image 'built_api:latest' built successfully." in proc.stdout


def test_deploy_prints_a_compile_summary_before_building(tmp_path):
    """`compile` and `serve` both print a summary of what actually got
    generated (endpoint list, background/task_id markers, dependencies)
    right after compiling -- see print_compile_summary in
    backend/inspector.py. `deploy` also compiles the notebook as its
    first step, but never called it, so a `deploy` run gave no visibility
    at all into what had been compiled before jumping straight to the
    Docker build -- only "Building Docker image ... built successfully."
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "import pandas as pd\n\n"
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n\n"
                            "def train_model(epochs: int) -> str:\n"
                            "    return 'done'\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)

    proc = _run_cli(
        ["deploy", str(notebook_path), "--output", "built_api"],
        cwd=workdir,
        path_dirs=[str(bin_dir)],
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    assert "Generated 2 endpoint(s):" in proc.stdout
    assert "POST /add" in proc.stdout
    assert "POST /train_model  [background]" in proc.stdout
    add_line = next(
        line for line in proc.stdout.splitlines() if line.strip() == "POST /add"
    )
    assert "[background]" not in add_line
    assert "Dependencies: pandas" in proc.stdout

    # The summary must appear before the build starts, not after.
    summary_index = proc.stdout.index("Generated 2 endpoint(s):")
    build_index = proc.stdout.index("Docker image 'built_api:latest' built successfully.")
    assert summary_index < build_index


def test_deploy_respects_custom_tag(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)

    proc = _run_cli(
        ["deploy", str(notebook_path), "--output", "generated", "--tag", "myapp:v2"],
        cwd=workdir,
        path_dirs=[str(bin_dir)],
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    log_lines = log_path.read_text(encoding="utf-8").splitlines()
    assert log_lines[:-1] == ["build", "-t", "myapp:v2", "."]


def test_deploy_reports_a_clear_error_when_docker_is_missing(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    # An empty PATH-equivalent directory so `docker` genuinely can't be found,
    # instead of falling back to whatever happens to be installed on the
    # machine running the tests.
    empty_bin_dir = tmp_path / "empty_bin"
    empty_bin_dir.mkdir()

    env = dict(os.environ)
    env["PATH"] = str(empty_bin_dir)
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", "deploy", str(notebook_path)],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 1
    assert "Traceback (most recent call last)" not in proc.stderr
    assert "Error: Docker CLI not found" in proc.stderr
    # The compile step must still have run before the docker lookup failed.
    assert (workdir / "generated" / "app.py").exists()


def _install_fake_docker_that_fails_build(bin_dir):
    """A fake `docker` whose `build` subcommand always exits non-zero, to
    exercise the subprocess.CalledProcessError path from `docker build`
    itself failing (as opposed to Docker not being installed at all).
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/bin/sh\n"
        'echo "docker: build failed: no space left on device" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_deploy_reports_a_clean_error_when_docker_build_fails(tmp_path):
    """Before CLI_USER_FACING_ERRORS existed, only "Docker CLI not found"
    (a FileNotFoundError converted to RuntimeError) was ever caught -- a
    `docker build` that ran but exited non-zero raised an uncaught
    subprocess.CalledProcessError, dumping a raw traceback instead of a
    clean error.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    bin_dir = tmp_path / "fakebin"
    _install_fake_docker_that_fails_build(bin_dir)

    proc = _run_cli(
        ["deploy", str(notebook_path), "--output", "generated"],
        cwd=workdir,
        path_dirs=[str(bin_dir)],
    )

    assert proc.returncode == 1
    assert "Traceback (most recent call last)" not in proc.stderr
    assert "Error: Command" in proc.stderr
    assert "returned non-zero exit status 1" in proc.stderr
    # The compile step must still have run before the failed docker build.
    assert (workdir / "generated" / "app.py").exists()


def _install_fake_docker_that_hangs(bin_dir, seconds):
    """A fake `docker` that sleeps instead of returning, to exercise the
    subprocess timeout -- before it existed, `deploy`'s docker build/push
    calls had no timeout at all, so a hung `docker build` (e.g. a stuck
    base-image pull) blocked the CLI forever with no way to configure a
    limit, unlike POST /api/deploy.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        f"#!/bin/sh\nsleep {seconds}\nexit 0\n",
        encoding="utf-8",
    )
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_deploy_reports_a_clean_error_when_docker_build_times_out(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    bin_dir = tmp_path / "fakebin"
    _install_fake_docker_that_hangs(bin_dir, seconds=5)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    env["NOTEBOOK_API_DEPLOY_TIMEOUT_SECONDS"] = "1"

    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", "deploy", str(notebook_path), "--output", "generated"],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 1
    assert "Traceback (most recent call last)" not in proc.stderr
    assert "Error: " in proc.stderr
    # subprocess.TimeoutExpired reports the actual elapsed wall-clock time
    # (e.g. "0.9999847 seconds"), not the configured value verbatim, so
    # this checks the message shape rather than an exact "1 seconds" match
    # -- which is inherently timing-dependent and would be flaky.
    assert "docker" in proc.stderr
    assert "timed out after" in proc.stderr
    assert "seconds" in proc.stderr
    # The compile step must still have run before the docker build hung.
    assert (workdir / "generated" / "app.py").exists()


def test_deploy_docker_timeout_is_configurable_via_env_var(tmp_path):
    """Same NOTEBOOK_API_DEPLOY_TIMEOUT_SECONDS env var POST /api/deploy
    already reads (see DEPLOY_SUBPROCESS_TIMEOUT_SECONDS in
    routes/upload.py) -- a docker call that would exceed the old, fixed
    600s default but comfortably finishes within a longer configured
    timeout must still succeed, not be arbitrarily cut off.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    bin_dir = tmp_path / "fakebin"
    _install_fake_docker_that_hangs(bin_dir, seconds=1)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    env["NOTEBOOK_API_DEPLOY_TIMEOUT_SECONDS"] = "30"

    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", "deploy", str(notebook_path), "--output", "generated"],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "built successfully" in proc.stdout


def test_deploy_reports_a_clean_error_for_a_missing_notebook(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    proc = _run_cli(
        ["deploy", str(workdir / "does-not-exist.ipynb")], cwd=workdir
    )

    assert proc.returncode == 1
    assert "Traceback (most recent call last)" not in proc.stderr
    assert "Error: " in proc.stderr
    assert "No such file or directory" in proc.stderr


def test_deploy_does_not_push_by_default(tmp_path):
    """Without --push, only `docker build` should run -- no `docker push`
    call at all.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker_recording_all_calls(bin_dir, log_path)

    proc = _run_cli(
        ["deploy", str(notebook_path), "--output", "generated"],
        cwd=workdir,
        path_dirs=[str(bin_dir)],
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    calls = [block for block in log_path.read_text(encoding="utf-8").split("==CALL==\n") if block]
    assert len(calls) == 1
    assert "Pushing Docker image" not in proc.stdout
    assert "pushed successfully" not in proc.stdout


def test_deploy_push_runs_docker_push_after_a_successful_build(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker_recording_all_calls(bin_dir, log_path)

    proc = _run_cli(
        [
            "deploy", str(notebook_path), "--output", "generated",
            "--tag", "registry.example.com/myapp:v1", "--push",
        ],
        cwd=workdir,
        path_dirs=[str(bin_dir)],
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    calls = [block for block in log_path.read_text(encoding="utf-8").split("==CALL==\n") if block]
    assert len(calls) == 2

    build_call = calls[0].splitlines()
    assert build_call[:-1] == ["build", "-t", "registry.example.com/myapp:v1", "."]
    assert build_call[-1] == str((workdir / "generated").resolve())

    push_call = calls[1].splitlines()
    assert push_call[:-1] == ["push", "registry.example.com/myapp:v1"]
    assert push_call[-1] == str((workdir / "generated").resolve())

    assert "Docker image 'registry.example.com/myapp:v1' pushed successfully." in proc.stdout


def test_deploy_push_help_documents_the_flag(tmp_path):

    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", "deploy", "--help"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--push" in proc.stdout


def test_deploy_json_flag_emits_machine_readable_output(tmp_path):
    """Before --json existed on `deploy`, a script driving it (CI, another
    tool shelling out to it) had no way to get its outcome (the tag that
    was actually built, whether it was pushed) as structured data -- only
    free-form progress text -- even though POST /api/deploy's REST
    response (routes/upload.py) already returns exactly this
    {"status", "tag", "pushed"} shape for the same operation. Matches
    that shape rather than inventing a different one, and none of
    compile_notebook's/print_compile_summary's/this command's own
    progress prints may leak onto stdout, or a script doing
    json.loads(stdout) would choke on it -- the same guarantee
    `compile --json` already makes.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker(bin_dir, log_path)

    proc = _run_cli(
        ["deploy", str(notebook_path), "--output", "built_api", "--json"],
        cwd=workdir,
        path_dirs=[str(bin_dir)],
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (workdir / "built_api" / "app.py").exists()

    data = json.loads(proc.stdout)
    assert data == {
        "status": "success",
        "tag": "built_api:latest",
        "pushed": False,
    }


def test_deploy_json_flag_reports_pushed_true_after_a_successful_push(tmp_path):

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    notebook_path = workdir / "nb.ipynb"
    _write_notebook(notebook_path)

    bin_dir = tmp_path / "fakebin"
    log_path = tmp_path / "docker_invocation.log"
    _install_fake_docker_recording_all_calls(bin_dir, log_path)

    proc = _run_cli(
        [
            "deploy", str(notebook_path), "--output", "generated",
            "--tag", "registry.example.com/myapp:v1", "--push", "--json",
        ],
        cwd=workdir,
        path_dirs=[str(bin_dir)],
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr

    data = json.loads(proc.stdout)
    assert data == {
        "status": "success",
        "tag": "registry.example.com/myapp:v1",
        "pushed": True,
    }

    calls = [block for block in log_path.read_text(encoding="utf-8").split("==CALL==\n") if block]
    assert len(calls) == 2
