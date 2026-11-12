"""`serve` passes isolation/limit settings through unchanged and fails fast on unusable ones."""
import pytest

from backend import serve as serve_module

LIMIT_VARS = {
    "NOTEBOOK_API_ISOLATE_EXECUTION": "1",
    "NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB": "512",
    "NOTEBOOK_API_ISOLATE_CPU_LIMIT_SECONDS": "5",
    "NOTEBOOK_API_ISOLATE_MAX_PROCESSES": "3",
    "NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS": "7",
}


class _Observer:
    def schedule(self, *a, **k): pass
    def start(self): pass
    def stop(self): pass
    def join(self, *a, **k): pass


class _Popen:
    launched = []

    def __init__(self, cmd, *args, **kwargs):
        self.cmd, self.env = cmd, kwargs.get("env")
        self.returncode = None
        _Popen.launched.append(self)

    def poll(self): return None
    def terminate(self): pass
    def kill(self): pass
    def wait(self, *a, **k): return 0


def _serve(monkeypatch, tmp_path, env):
    _Popen.launched = []
    for name in list(LIMIT_VARS) + ["NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS"]:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    compiled = []
    monkeypatch.setattr(serve_module, "compile_notebook", lambda *a, **k: compiled.append(a))
    monkeypatch.setattr(serve_module, "print_compile_summary", lambda *a, **k: None)
    monkeypatch.setattr(serve_module, "Observer", _Observer)
    monkeypatch.setattr(serve_module.subprocess, "Popen", _Popen)

    def stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(serve_module.time, "sleep", stop)
    nb = tmp_path / "nb.ipynb"
    nb.write_text("{}", encoding="utf-8")
    serve_module.serve_notebook(str(nb), str(tmp_path / "out"))
    return compiled


def test_serve_hands_every_limit_to_the_server_process_unchanged(monkeypatch, tmp_path):
    compiled = _serve(monkeypatch, tmp_path, LIMIT_VARS)
    assert compiled and len(_Popen.launched) == 1
    env = _Popen.launched[0].env
    assert {k: env[k] for k in LIMIT_VARS} == LIMIT_VARS  # explicit values, not defaults
    assert "--reload" in _Popen.launched[0].cmd  # the reloader child inherits this env on every reload


def test_unset_limits_are_not_invented(monkeypatch, tmp_path):
    _serve(monkeypatch, tmp_path, {})
    env = _Popen.launched[0].env
    assert not any(k in env for k in LIMIT_VARS)


@pytest.mark.parametrize("env, message", [
    ({"NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB": "lots"}, "NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB"),
    ({"NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB": "512"}, "no effect unless NOTEBOOK_API_ISOLATE_EXECUTION=1"),
    ({"NOTEBOOK_API_ISOLATE_MAX_PROCESSES": "2"}, "no effect unless"),
    ({"NOTEBOOK_API_ISOLATE_EXECUTION": "1", "NOTEBOOK_API_ISOLATE_CPU_LIMIT_SECONDS": "-1"}, "NOTEBOOK_API_ISOLATE_CPU_LIMIT_SECONDS"),
])
def test_unusable_limit_settings_fail_before_compiling_or_launching(monkeypatch, tmp_path, env, message):
    _Popen.launched = []
    compiled = []
    for name in LIMIT_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(serve_module, "compile_notebook", lambda *a, **k: compiled.append(a))
    nb = tmp_path / "nb.ipynb"
    nb.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        serve_module.serve_notebook(str(nb), str(tmp_path / "out"))
    assert compiled == [] and _Popen.launched == []


def test_unsupported_platform_is_reported_accurately(monkeypatch):
    monkeypatch.setattr(
        "backend.runtime.execution_limits.isolation_platform_problem",
        lambda *a, **k: "this platform lacks process groups (os.killpg)",
    )
    with pytest.raises(ValueError, match="not supported here: this platform lacks process groups"):
        serve_module.check_execution_limit_env({"NOTEBOOK_API_ISOLATE_EXECUTION": "1"})
    # With isolation off the platform does not matter, and nothing is claimed enforced.
    serve_module.check_execution_limit_env({})
