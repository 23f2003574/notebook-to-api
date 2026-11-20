"""`--watch` commands stop cleanly on Ctrl+C at any point, without any fixed sleeps.

Every wait here is for an observable event (a request arrived, N requests were served), so the
tests behave the same on an idle machine and a heavily loaded one."""
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BODIES = {
    "/health": {"status": "healthy"},
    "/ready": {"status": "ready", "tasks_registered": 0},
    "/info": {"service": "s", "version": "0.1.0", "endpoint_count": 0, "background_endpoint_count": 0},
    "/config": {
        "max_request_body_bytes": 1, "task_ttl_seconds": 1, "max_pending_tasks": 1,
        "task_execution_timeout_seconds": None, "webhook_timeout_seconds": 1, "webhook_signing_enabled": False,
        "webhook_max_retries": 0, "rate_limit_per_minute": 0, "allowed_origins": [], "disable_docs": False,
    },
}


class _Server:
    """A fake app. `gate`, when set, makes every request block until the test opens it."""

    def __init__(self):
        outer = self
        self.requests = []
        self.arrived = threading.Event()
        self.gate = None
        self.lock = threading.Lock()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                with outer.lock:
                    outer.requests.append(self.path)
                outer.arrived.set()
                if outer.gate is not None:
                    outer.gate.wait(timeout=30)
                body = json.dumps(BODIES.get(self.path, {})).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the CLI was interrupted mid-request: expected

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        if self.gate is not None:
            self.gate.set()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.close()


def _start(server, tmp_path, interval="0.01"):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.Popen(
        [sys.executable, "-m", "backend.cli", "app-status", "--host", "127.0.0.1", "--port", str(server.port),
         "--watch", "--interval", interval],
        cwd=str(tmp_path), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _wait_for(predicate, proc, what, timeout=120):
    deadline = time.monotonic() + timeout
    while not predicate():
        if proc.poll() is not None:
            out, err = proc.communicate()
            raise AssertionError(f"exited early ({proc.returncode}) before {what}: {out}{err}")
        if time.monotonic() > deadline:
            proc.kill()
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.005)


def test_rapid_polling_never_stops_by_itself_and_ends_cleanly_on_interrupt(server, tmp_path):
    proc = _start(server, tmp_path, interval="0.001")
    _wait_for(lambda: len(server.requests) >= 80, proc, "20 polls")
    proc.send_signal(signal.SIGINT)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, out + err
    assert out.count("Compiled app at") >= 15 and "Stopped watching." in out
    assert "Traceback" not in err
    assert server.requests[:4] == ["/health", "/ready", "/info", "/config"]  # every poll asks in the same order


@pytest.mark.parametrize("attempt", range(3))
def test_interrupt_while_a_request_is_in_flight_is_a_clean_stop(server, tmp_path, attempt):
    server.gate = threading.Event()
    proc = _start(server, tmp_path)
    _wait_for(server.arrived.is_set, proc, "the first request to arrive")
    proc.send_signal(signal.SIGINT)  # the CLI is blocked inside that request
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, out + err
    assert "Stopped watching." in out and "Traceback" not in err
    assert len(server.requests) == 1  # it did not start another poll after the interrupt


def test_a_second_interrupt_after_stopping_is_harmless(server, tmp_path):
    proc = _start(server, tmp_path)
    _wait_for(lambda: len(server.requests) >= 8, proc, "two polls")
    proc.send_signal(signal.SIGINT)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0 and out.count("Stopped watching.") == 1


def test_watch_ends_with_a_clear_error_when_the_app_goes_away(server, tmp_path):
    proc = _start(server, tmp_path)
    _wait_for(lambda: len(server.requests) >= 4, proc, "one poll")
    server.httpd.shutdown()
    server.httpd.server_close()
    out, err = proc.communicate(timeout=60)
    assert proc.returncode != 0
    assert "Could not reach the compiled app" in out + err and "Traceback" not in err


def test_children_do_not_inherit_an_ignored_sigint():
    """Regression: under `nohup pytest &` SIGINT was inherited as ignored, so interrupting a
    watching child never stopped it."""
    out = subprocess.run([sys.executable, "-c", "import signal; print(signal.getsignal(signal.SIGINT))"],
                         capture_output=True, text=True, timeout=60).stdout
    assert "default_int_handler" in out
