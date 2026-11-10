import multiprocessing as mp
import os
import time

import pytest
from fastapi.testclient import TestClient

from backend.generator.api_generator import generate_fastapi_code
from tests.test_generator import _register_fake_notebook_module

HEADERS = {"X-API-Key": "notebook-to-api-dev-key"}


def _client(monkeypatch, timeout="1"):
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_EXECUTION", "1")
    monkeypatch.setenv("NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS", timeout)
    functions = [
        {"name": "add", "args": [{"name": "a", "type": "int"}, {"name": "b", "type": "int"}], "return_type": "int"},
        {"name": "hang", "args": [], "return_type": "dict"},
        {"name": "crash", "args": [], "return_type": "dict"},
    ]
    code = generate_fastapi_code(functions)
    _register_fake_notebook_module(monkeypatch)
    ns = {"__name__": "generated_app"}
    exec(compile(code, "<generated>", "exec"), ns)
    nb = ns["notebook_module"]
    nb.add = lambda a, b: a + b
    nb.hang = lambda: time.sleep(60)
    nb.crash = lambda: os._exit(7)
    return TestClient(ns["app"])


def _no_children():
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not mp.active_children():
            return True
        time.sleep(0.05)
    return False


def test_isolated_success_keeps_contract(monkeypatch):
    r = _client(monkeypatch).post("/add", json={"a": 2, "b": 3}, headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"result": 5}


def test_timeout_is_504_child_reaped_and_next_request_works(monkeypatch):
    client = _client(monkeypatch)
    start = time.monotonic()
    r = client.post("/hang", json={}, headers=HEADERS)
    assert r.status_code == 504
    assert time.monotonic() - start < 10
    assert _no_children()
    assert client.post("/add", json={"a": 1, "b": 1}, headers=HEADERS).json() == {"result": 2}


def test_child_crash_is_controlled_500_without_traceback(monkeypatch):
    r = _client(monkeypatch).post("/crash", json={}, headers=HEADERS)
    assert r.status_code == 500
    assert "Traceback" not in r.text
    assert _no_children()


def test_auth_and_validation_unchanged(monkeypatch):
    client = _client(monkeypatch)
    assert client.post("/add", json={"a": 1, "b": 2}).status_code == 401
    assert client.post("/add", json={"a": "x", "b": 2}, headers=HEADERS).status_code == 422
