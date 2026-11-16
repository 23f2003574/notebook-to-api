"""/metrics and /metrics/prometheus break request counts, 5xx errors and latency down by route."""
import importlib
import sys

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook

HEADERS = {"X-API-Key": "k"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    src = "def ok(x: int) -> int:\n    return x\ndef boom(x: int) -> int:\n    return 1 // x\n"
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(src)]), str(tmp_path / "nb.ipynb"))
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "endpoint_metrics_app"))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "k")
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("endpoint_metrics_app.app")
    yield TestClient(module.app, raise_server_exceptions=False)
    for name in [m for m in sys.modules if m.startswith("endpoint_metrics_app")]:
        del sys.modules[name]


def test_metrics_report_each_route_separately(client):
    for _ in range(2):
        assert client.post("/ok", json={"x": 1}, headers=HEADERS).status_code == 200
    assert client.post("/boom", json={"x": 0}, headers=HEADERS).status_code == 500
    assert client.get("/no-such-path", headers=HEADERS).status_code == 404

    by_route = client.get("/metrics", headers=HEADERS).json()["http_requests_by_endpoint"]
    assert by_route["/ok"]["requests"] == 2 and by_route["/ok"]["errors_5xx"] == 0
    assert by_route["/boom"]["requests"] == 1 and by_route["/boom"]["errors_5xx"] == 1
    assert by_route["(unmatched)"]["requests"] == 1  # unknown paths cannot create unbounded series
    assert "/no-such-path" not in by_route
    assert by_route["/ok"]["duration_ms_sum"] >= 0


def test_prometheus_exposes_the_same_series(client):
    client.post("/boom", json={"x": 0}, headers=HEADERS)
    text = client.get("/metrics/prometheus", headers=HEADERS).text
    assert 'notebook_api_endpoint_requests_total{route="/boom"} 1' in text
    assert 'notebook_api_endpoint_errors_total{route="/boom"} 1' in text
    assert "# TYPE notebook_api_endpoint_duration_ms_sum counter" in text
