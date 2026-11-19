"""Isolated notebook code cannot read the service's own credentials."""
import os

import pytest

from backend.runtime.isolated_execution import run_isolated, scrub_service_secrets

pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="isolation needs fork")


def _read(name):
    return os.environ.get(name)


@pytest.fixture
def env(monkeypatch):
    for k, v in {
        "NOTEBOOK_API_KEY": "svc-key", "NOTEBOOK_API_WEBHOOK_SECRET": "hook",
        "NOTEBOOK_API_DASHBOARD_KEY": "dash", "NOTEBOOK_API_PORT": "8000",
        "NOTEBOOK_API_MAX_TASKS": "5", "OPENAI_API_KEY": "mine", "PLAIN": "x",
    }.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize("name", ["NOTEBOOK_API_KEY", "NOTEBOOK_API_WEBHOOK_SECRET", "NOTEBOOK_API_DASHBOARD_KEY"])
def test_service_credentials_are_hidden_in_child(env, name):
    assert run_isolated(_read, (name,), timeout=10) is None
    assert os.environ[name]  # the parent is unaffected


@pytest.mark.parametrize("name,value", [("NOTEBOOK_API_PORT", "8000"), ("NOTEBOOK_API_MAX_TASKS", "5"),
                                        ("OPENAI_API_KEY", "mine"), ("PLAIN", "x")])
def test_other_variables_are_kept(env, name, value):
    assert run_isolated(_read, (name,), timeout=10) == value


def test_allowlisted_variable_remains_readable(env, monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_ALLOW_ENV", "NOTEBOOK_API_WEBHOOK_SECRET, OTHER")
    assert run_isolated(_read, ("NOTEBOOK_API_WEBHOOK_SECRET",), timeout=10) == "hook"
    assert run_isolated(_read, ("NOTEBOOK_API_KEY",), timeout=10) is None


def test_subprocesses_started_by_notebook_code_inherit_the_scrubbed_environment(env):
    import subprocess
    import sys

    def spawn():
        return subprocess.run([sys.executable, "-c", "import os;print(os.environ.get('NOTEBOOK_API_KEY'))"],
                              capture_output=True, text=True).stdout.strip()

    assert run_isolated(spawn, (), timeout=20) == "None"


def test_scrub_returns_removed_names_for_a_plain_mapping():
    env = {"NOTEBOOK_API_KEY": "a", "NOTEBOOK_API_TASK_TTL_SECONDS": "1", "NOTEBOOK_API_ISOLATE_ALLOW_ENV": "NOTEBOOK_API_KEY2",
           "NOTEBOOK_API_KEY2": "b"}
    assert scrub_service_secrets(env) == ["NOTEBOOK_API_KEY"]
    assert "NOTEBOOK_API_TASK_TTL_SECONDS" in env and "NOTEBOOK_API_KEY2" in env
