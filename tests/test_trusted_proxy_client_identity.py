"""The failed-authentication throttle keys on the real client, and only trusted proxies can name one."""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend import dashboard
from backend.compiler import compile_notebook

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("proxy")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def ping(x: int) -> int:\n    return x\n"))
    nbformat.write(nb, tmp / "nb.ipynb")
    compile_notebook(str(tmp / "nb.ipynb"), str(tmp / "proxyapp"))
    return tmp


def _load(built, monkeypatch, trusted):
    monkeypatch.setenv("NOTEBOOK_API_KEY", "right-key")
    monkeypatch.setenv("NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE", "3")
    monkeypatch.setenv("NOTEBOOK_API_TRUSTED_PROXIES", trusted)
    monkeypatch.syspath_prepend(str(built))
    spec = importlib.util.spec_from_file_location("proxyapp_app", built / "proxyapp" / "app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "proxyapp_app", module)
    spec.loader.exec_module(module)
    return module


def _fail(client, n, forwarded=None):
    headers = {"X-API-Key": "bad"}
    if forwarded:
        headers["X-Forwarded-For"] = forwarded
    return [client.post("/ping", json={"x": 1}, headers=headers).status_code for _ in range(n)]


def test_clients_behind_a_trusted_proxy_get_separate_buckets(built, monkeypatch):
    module = _load(built, monkeypatch, "10.0.0.0/8")
    client = TestClient(module.app, client=("10.1.2.3", 1))
    assert _fail(client, 4, "203.0.113.1") == [401, 401, 401, 429]
    assert _fail(client, 2, "203.0.113.2") == [401, 401]  # a different caller is unaffected
    assert client.post("/ping", json={"x": 1}, headers={"X-API-Key": "right-key", "X-Forwarded-For": "203.0.113.9"}).status_code == 200


def test_an_untrusted_sender_cannot_choose_its_bucket_with_a_forged_header(built, monkeypatch):
    module = _load(built, monkeypatch, "10.0.0.0/8")
    client = TestClient(module.app, client=("198.51.100.7", 1))
    codes = [client.post("/ping", json={"x": 1}, headers={"X-API-Key": "bad", "X-Forwarded-For": f"203.0.113.{i}"}).status_code for i in range(5)]
    assert codes == [401, 401, 401, 429, 429]  # one bucket: the real peer


def test_default_trusts_no_proxy(built, monkeypatch):
    module = _load(built, monkeypatch, "")
    client = TestClient(module.app, client=("10.1.2.3", 1))
    codes = [client.post("/ping", json={"x": 1}, headers={"X-API-Key": "bad", "X-Forwarded-For": f"203.0.113.{i}"}).status_code for i in range(5)]
    assert codes == [401, 401, 401, 429, 429]


def test_only_the_rightmost_untrusted_hop_counts_and_garbage_falls_back_to_the_proxy(built, monkeypatch):
    module = _load(built, monkeypatch, "10.0.0.0/8,2001:db8::/32")

    class Req:
        def __init__(self, peer, forwarded):
            self.client = type("C", (), {"host": peer})()
            self.headers = {"x-forwarded-for": forwarded}

    ident = module._auth_client_id
    assert ident(Req("10.0.0.1", "1.1.1.1, 203.0.113.5, 10.0.0.2")) == "203.0.113.5"  # "1.1.1.1" is attacker-supplied
    assert ident(Req("10.0.0.1", "not-an-ip")) == "10.0.0.1"
    assert ident(Req("10.0.0.1", "")) == "10.0.0.1"
    assert ident(Req("2001:db8::1", "198.51.100.9")) == "198.51.100.9"
    assert ident(Req("192.0.2.1", "198.51.100.9")) == "192.0.2.1"
    with pytest.raises(RuntimeError, match="TRUSTED_PROXIES"):
        module._parse_trusted_proxies("not-a-network")


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _real_server(built, trusted, extra_args=()):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": f"{built}:{ROOT}", "PYTHONDONTWRITEBYTECODE": "1", "NOTEBOOK_API_KEY": "right-key",
           "NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE": "3", "NOTEBOOK_API_TRUSTED_PROXIES": trusted}
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "proxyapp.app:app", "--port", str(port), "--log-level", "warning", *extra_args],
                               cwd=built, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return process, port
        except OSError:
            time.sleep(0.3)
    process.kill()
    raise AssertionError(process.stdout.read().decode())


def _post(port, key, forwarded):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/ping", method="POST", data=b'{"x": 1}',
                                     headers={"X-API-Key": key, "X-Forwarded-For": forwarded, "Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(request, timeout=5).status
    except urllib.error.HTTPError as error:
        return error.code


def test_real_server_with_the_loopback_proxy_trusted_separates_clients(built):
    process, port = _real_server(built, "127.0.0.1", ("--no-proxy-headers",))
    try:
        assert [_post(port, "bad", "203.0.113.1") for _ in range(4)] == [401, 401, 401, 429]
        assert [_post(port, "bad", "203.0.113.2") for _ in range(2)] == [401, 401]
    finally:
        process.terminate()
        process.wait(timeout=20)


def test_real_server_default_ignores_forwarded_headers_even_from_loopback(built):
    process, port = _real_server(built, "", ("--no-proxy-headers",))
    try:
        assert [_post(port, "bad", f"203.0.113.{i}") for i in range(5)] == [401, 401, 401, 429, 429]
    finally:
        process.terminate()
        process.wait(timeout=20)


def test_generated_dockerfile_disables_uvicorns_own_proxy_header_handling(built):
    assert "--no-proxy-headers" in (built / "proxyapp" / "Dockerfile").read_text()


def test_generated_deployments_expose_the_setting(built):
    for name in ("docker-compose.yml", ".env.example", "kubernetes.yaml"):
        assert "NOTEBOOK_API_TRUSTED_PROXIES" in (built / "proxyapp" / name).read_text(), name


def test_dashboard_throttle_follows_the_same_rules(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_TRUSTED_PROXIES", "10.0.0.0/8")

    class Req:
        def __init__(self, peer, forwarded):
            self.client = type("C", (), {"host": peer})()
            self.headers = {"x-forwarded-for": forwarded}

    assert dashboard._auth_client(Req("10.0.0.5", "1.2.3.4, 203.0.113.8")) == "203.0.113.8"
    assert dashboard._auth_client(Req("198.51.100.1", "203.0.113.8")) == "198.51.100.1"
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_TRUSTED_PROXIES", "")
    assert dashboard._auth_client(Req("10.0.0.5", "203.0.113.8")) == "10.0.0.5"
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_TRUSTED_PROXIES", "bogus")
    with pytest.raises(RuntimeError, match="TRUSTED_PROXIES"):
        dashboard.dashboard_trusted_proxies()
