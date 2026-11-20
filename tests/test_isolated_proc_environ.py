"""Service credentials are not readable from /proc/<pid>/environ by isolated notebook code."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not (sys.platform.startswith("linux") and os.path.exists("/proc/self/environ")), reason="needs Linux /proc"
)


def _run(script, **env):
    code = "import sys, os, json\nsys.path.insert(0, %r)\n" % str(ROOT) + textwrap.dedent(script)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **env}, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


PEEK = """
from backend.runtime.isolated_execution import run_isolated
def peek():
    seen = {}
    for label, path in (("self", "/proc/self/environ"), ("parent", f"/proc/{os.getppid()}/environ")):
        raw = open(path, "rb").read()
        seen[label] = [name.decode() for name in (b"SECRETKEY", b"HOOKSECRET", b"ALLOWED", b"PLAINVALUE", b"OPENAIVALUE") if name in raw]
    seen["env"] = {k: os.environ.get(k) for k in ("NOTEBOOK_API_KEY", "NOTEBOOK_API_WEBHOOK_SECRET", "NOTEBOOK_API_ALLOWED_TOKEN", "PLAIN", "OPENAI_API_KEY")}
    return seen
result = run_isolated(peek, (), timeout=20)
result["parent_env_key"] = os.environ.get("NOTEBOOK_API_KEY")
print(json.dumps(result))
"""

ENV = {"NOTEBOOK_API_KEY": "SECRETKEY", "NOTEBOOK_API_WEBHOOK_SECRET": "HOOKSECRET",
       "NOTEBOOK_API_ALLOWED_TOKEN": "ALLOWED", "NOTEBOOK_API_ISOLATE_ALLOW_ENV": "NOTEBOOK_API_ALLOWED_TOKEN",
       "PLAIN": "PLAINVALUE", "OPENAI_API_KEY": "OPENAIVALUE"}


def test_credentials_are_hidden_from_self_and_parent_proc_environ_but_other_values_stay():
    seen = _run(PEEK, **ENV)
    assert set(seen["self"]) == {"ALLOWED", "PLAINVALUE", "OPENAIVALUE"}
    assert set(seen["parent"]) == {"ALLOWED", "PLAINVALUE", "OPENAIVALUE"}
    assert seen["env"]["NOTEBOOK_API_KEY"] is None and seen["env"]["NOTEBOOK_API_WEBHOOK_SECRET"] is None
    assert seen["env"]["NOTEBOOK_API_ALLOWED_TOKEN"] == "ALLOWED" and seen["env"]["PLAIN"] == "PLAINVALUE"
    assert seen["env"]["OPENAI_API_KEY"] == "OPENAIVALUE"
    assert seen["parent_env_key"] == "SECRETKEY"  # the service itself still has its settings


def test_timeout_memory_limit_and_error_paths_still_work_after_hiding():
    out = _run("""
        from backend.runtime.isolated_execution import run_isolated, IsolatedExecutionTimeout, IsolatedExecutionError
        import time
        def spin(): time.sleep(30)
        def hog(): return len(bytearray(900 * 1024 * 1024))
        def boom(): raise ValueError("bad")
        res = {}
        try: run_isolated(spin, (), timeout=0.5)
        except IsolatedExecutionTimeout: res["timeout"] = True
        try: run_isolated(hog, (), timeout=20, memory_limit_mb=300)
        except IsolatedExecutionError as e: res["memory"] = "memory" in str(e)
        try: run_isolated(boom, (), timeout=10)
        except IsolatedExecutionError as e: res["error"] = "ValueError" in str(e)
        res["ok"] = run_isolated(lambda: 7, (), timeout=10)
        print(json.dumps(res))
    """, **ENV)
    assert out == {"timeout": True, "memory": True, "error": True, "ok": 7}


def test_cancellation_still_terminates_the_child():
    out = _run("""
        import threading, time
        from backend.runtime.isolated_execution import run_isolated, IsolatedExecutionCancelled
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        try: run_isolated(lambda: time.sleep(30), (), timeout=30, cancel_event=cancel); print(json.dumps({"cancelled": False}))
        except IsolatedExecutionCancelled: print(json.dumps({"cancelled": True}))
    """, **ENV)
    assert out == {"cancelled": True}


def test_worker_children_are_covered_too():
    out = _run("""
        from backend.runtime.isolated_execution import IsolatedWorker
        def handler(_): 
            return [b"SECRETKEY" in open("/proc/self/environ", "rb").read(), b"SECRETKEY" in open(f"/proc/{os.getppid()}/environ", "rb").read()]
        worker = IsolatedWorker(handler)
        print(json.dumps(worker.request(None, timeout=10)))
    """, **ENV)
    assert out == [False, False]


def test_it_fails_closed_when_the_block_cannot_be_blanked():
    out = _run("""
        import ctypes
        from backend.runtime import isolated_execution as ie
        ctypes.memset = lambda *a, **k: None
        try: ie.hide_service_secrets_in_proc_environ(); print(json.dumps("silent"))
        except ie.IsolatedExecutionError as e: print(json.dumps("refused"))
    """, **ENV)
    assert out == "refused"


def test_no_credentials_means_nothing_to_hide():
    assert _run("""
        from backend.runtime.isolated_execution import hide_service_secrets_in_proc_environ
        print(json.dumps(hide_service_secrets_in_proc_environ()))
    """, **{k: "" for k in ENV}) == []
