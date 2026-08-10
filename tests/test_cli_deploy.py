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

    assert proc.returncode != 0
    assert "Docker CLI not found" in proc.stderr
    # The compile step must still have run before the docker lookup failed.
    assert (workdir / "generated" / "app.py").exists()


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
