"""Notebook -> compile -> generated app -> OpenAPI -> generated Python SDK ->
call the app through that SDK. Each stage has unit tests; this checks they
agree with each other (payload shape, auth header, result unwrapping, task
polling), which no single-stage test can."""

import importlib
import sys

import nbformat
import pytest
import requests
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook
from backend.exporters.sdk_generator import generate_python_sdk

NOTEBOOK_SOURCE = '''
import datetime
from typing import Optional

def add(a: int, b: int = 2) -> int:
    return a + b

def greet(name: Optional[str] = None, excited: bool = False) -> str:
    return "hi " + (name or "you") + ("!" if excited else "")

def when(day: datetime.date) -> str:
    return day.isoformat()

def class_(from_: int) -> int:
    return from_ + 1

# notebook-to-api: background
def slow_total(items: list[int]) -> int:
    return sum(items)
'''


@pytest.fixture
def sdk_client(tmp_path, monkeypatch):
    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(nbformat.v4.new_code_cell(NOTEBOOK_SOURCE))
    notebook_path = tmp_path / "nb.ipynb"
    nbformat.write(notebook, str(notebook_path))
    package = "workflow_svc"
    compile_notebook(str(notebook_path), str(tmp_path / package))

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("NOTEBOOK_API_KEY", "wf-key")
    for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
        monkeypatch.delitem(sys.modules, name)
    app = importlib.import_module(f"{package}.app").app
    http = TestClient(app, base_url="http://api.test")

    spec_path = tmp_path / "openapi.json"
    spec_path.write_text(
        http.get("/openapi.json", headers={"X-API-Key": "wf-key"}).text, encoding="utf-8"
    )
    sdk_path = tmp_path / "client.py"
    generate_python_sdk(str(spec_path), str(sdk_path))

    # Route the SDK's real `requests` calls into the in-process app.
    for verb in ("get", "post", "delete"):
        monkeypatch.setattr(
            requests, verb,
            lambda url, _verb=verb, **kw: getattr(http, _verb)(
                url, **{k: v for k, v in kw.items() if k in ("json", "headers", "params")}
            ),
        )
    namespace = {}
    exec(compile(sdk_path.read_text(encoding="utf-8"), str(sdk_path), "exec"), namespace)
    yield namespace["NotebookAPIClient"]("http://api.test", api_key="wf-key")
    for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
        sys.modules.pop(name, None)


def test_sdk_calls_the_compiled_app_end_to_end(sdk_client):
    assert sdk_client.add({"a": 1}) == {"result": 3}
    assert sdk_client.add({"a": 1, "b": 10}) == {"result": 11}
    assert sdk_client.greet({}) == {"result": "hi you"}
    assert sdk_client.greet({"name": None, "excited": True}) == {"result": "hi you!"}
    assert sdk_client.when({"day": "2024-01-02"}) == {"result": "2024-01-02"}
    # A function named like a Python keyword still gets a callable client method.
    assert sdk_client.class_({"from_": 1}) == {"result": 2}


def test_sdk_gets_a_background_result_through_wait_for_task(sdk_client):
    submitted = sdk_client.slow_total({"items": [1, 2, 3]})

    assert submitted["status"] == "processing"
    finished = sdk_client.wait_for_task(submitted["task_id"], poll_interval=0.01, timeout=10)

    assert finished["status"] == "completed"
    assert finished["result"] == 6


def test_sdk_surfaces_a_rejected_request_as_an_error(sdk_client):
    with pytest.raises(Exception) as excinfo:
        sdk_client.add({"a": "not-a-number"})

    assert "422" in str(excinfo.value)
