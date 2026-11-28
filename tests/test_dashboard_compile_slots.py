"""The dashboard bounds concurrent compiles with kernel-released slots shared by every worker."""
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import dashboard, dashboard_compile_slots as slots

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_COMPILE_SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_MAX_CONCURRENT_COMPILES", "2")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_COMPILE_QUEUE_SECONDS", "0")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_PER_MINUTE", "0")
    for name in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEYS", "NOTEBOOK_API_DASHBOARD_KEYS_FILE"):
        monkeypatch.delenv(name, raising=False)
    for key in slots._STATS:
        slots._STATS[key] = 0


def test_requests_above_the_limit_are_rejected_and_capacity_returns_on_release():
    with slots.acquire():
        with slots.acquire():
            assert slots.in_use() == 2
            with pytest.raises(HTTPException) as busy:
                with slots.acquire():
                    pass
            assert busy.value.status_code == 503 and busy.value.headers["Retry-After"]
        assert slots.in_use() == 1
        with slots.acquire():  # one slot freed -> admitted again
            pass
    assert slots.in_use() == 0 and slots.stats()["rejected"] == 1


def test_a_failing_compile_releases_its_slot():
    for _ in range(5):  # more failures than slots: a leak would exhaust capacity
        with pytest.raises(RuntimeError):
            with slots.acquire():
                raise RuntimeError("compile failed")
    assert slots.in_use() == 0 and slots.stats()["failed"] == 5
    with slots.acquire():
        pass


def test_waiting_requests_are_admitted_when_a_slot_frees_within_the_queue_time(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_MAX_CONCURRENT_COMPILES", "1")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_COMPILE_QUEUE_SECONDS", "5")
    holding, release, outcome = threading.Event(), threading.Event(), []

    def holder():
        with slots.acquire():
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    holding.wait(5)
    threading.Timer(0.3, release.set).start()
    started = time.monotonic()
    with slots.acquire():
        outcome.append(time.monotonic() - started)
    thread.join()
    assert 0.2 < outcome[0] < 4  # it waited for the holder instead of failing


def test_a_killed_holder_cannot_leak_a_slot(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_MAX_CONCURRENT_COMPILES", "1")
    code = ("import sys,time; from backend import dashboard_compile_slots as s\n"
            "with s.acquire():\n    print('held', flush=True); time.sleep(60)")
    child = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    assert slots.in_use() == 1  # a different process holds it: the limit is shared
    with pytest.raises(HTTPException):
        with slots.acquire():
            pass
    child.kill()
    child.wait()
    with slots.acquire():  # the kernel released the dead process's lock
        pass


def test_zero_disables_the_limit(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_MAX_CONCURRENT_COMPILES", "0")
    with slots.acquire(), slots.acquire(), slots.acquire():
        assert slots.in_use() == 0


def test_the_compile_endpoint_answers_503_when_capacity_is_exhausted(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_MAX_CONCURRENT_COMPILES", "1")
    client = TestClient(dashboard.app)
    with slots.acquire():
        response = client.post("/api/compile", json={"notebook_path": "sample.ipynb"})
        assert response.status_code == 503 and "Retry-After" in response.headers
        capacity = client.get("/api/compile/capacity").json()
        assert capacity["limit"] == 1 and capacity["in_use"] == 1 and capacity["rejected"] == 1
    assert client.post("/api/compile", json={"notebook_path": "missing.ipynb"}).status_code != 503


def test_capacity_endpoint_needs_only_the_read_scope():
    assert dashboard.required_scope("GET", "/api/compile/capacity") == "read"


def test_a_real_disconnect_abandons_the_queued_compile(monkeypatch, tmp_path):
    import socket

    import uvicorn

    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_MAX_CONCURRENT_COMPILES", "1")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_COMPILE_QUEUE_SECONDS", "30")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(dashboard.app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        with slots.acquire():
            body = b'{"notebook_path": "n.ipynb"}'
            client = socket.create_connection(("127.0.0.1", port))
            client.sendall(b"POST /api/compile HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                           b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
            time.sleep(0.5)
            client.close()  # the caller gives up while the only slot is busy
            deadline = time.monotonic() + 10
            while slots.stats()["abandoned"] == 0 and time.monotonic() < deadline:
                time.sleep(0.05)
        assert slots.stats()["abandoned"] == 1
    finally:
        server.should_exit = True
        thread.join(10)


def test_the_configured_limit_is_honoured_without_fcntl(monkeypatch):
    monkeypatch.setattr(slots, "fcntl", None)
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_MAX_CONCURRENT_COMPILES", "3")
    slots._FALLBACKS.clear()
    with slots.acquire(), slots.acquire(), slots.acquire():
        assert slots.in_use() == 3
        with pytest.raises(HTTPException) as busy:
            with slots.acquire():
                pass
        assert busy.value.status_code == 503
    assert slots.in_use() == 0


def test_a_crashed_holder_frees_its_slot():
    holder = subprocess.Popen(
        [sys.executable, "-c", "from backend import dashboard_compile_slots as s\nimport time\n"
         "c = s.acquire(); c.__enter__(); print('held', flush=True); time.sleep(60)"],
        cwd=ROOT, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert slots.in_use(2) == 1
    finally:
        holder.kill()
        holder.wait()
    assert slots.in_use(2) == 0


def test_only_authorized_requests_queue_for_a_slot(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEY", "slot-test-key-0123456789")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_MAX_CONCURRENT_COMPILES", "1")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_COMPILE_QUEUE_SECONDS", "30")
    client = TestClient(dashboard.app)
    with slots.acquire():
        started = time.monotonic()
        assert client.post("/api/compile", json={"notebook_path": "n.ipynb"}).status_code == 401
        assert time.monotonic() - started < 5  # refused by authentication, never queued behind the busy slot
