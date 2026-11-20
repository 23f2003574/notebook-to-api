"""inspect, compile and validate report the same compiler diagnostics."""
import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app

client = TestClient(app)
CODE = (
    "from typing import Callable\n"
    "def train_x(a: int): return a\n"
    "# notebook-to-api: background\ndef slow(a: int): return a\n"
    "def prep(a: int): return a\n"
    "def bad(f: Callable): return 1\n"
    "x = input('name')\n"
)


@pytest.fixture
def uploaded(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_routes, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(upload_routes, "GENERATED_DIR", str(tmp_path / "generated"))
    (tmp_path / "uploads").mkdir()
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(CODE))
    assert client.post("/api/upload", files={"file": ("n.ipynb", nbformat.writes(nb).encode(), "application/json")}).status_code == 200
    return "n.ipynb"


def _responses(name):
    body = {"notebook_path": name}
    return {
        "inspect": client.post("/api/inspect", json=body).json(),
        "compile": client.post("/api/compile", json=body).json(),
        "validate": client.post("/api/validate", json=body).json(),
    }


def test_all_three_routes_agree_on_the_diagnostics(uploaded):
    out = _responses(uploaded)
    for route, data in out.items():
        assert data["inferred_background"] == ["train_x"], route  # not "slow": a directive says so
        assert [s["name"] for s in data["skipped_functions"]] == ["bad"], route
        assert any("input" in w for w in data["startup_warnings"]), route
    assert out["inspect"]["startup_warnings"] == out["compile"]["startup_warnings"] == out["validate"]["startup_warnings"]


def test_inferred_list_follows_only_and_exclude(uploaded):
    data = client.post("/api/compile", json={"notebook_path": uploaded, "exclude": ["train_x"]}).json()
    assert data["inferred_background"] == []
    data = client.post("/api/compile", json={"notebook_path": uploaded, "only": ["train_x"]}).json()
    assert data["inferred_background"] == ["train_x"]


def test_clean_notebook_has_empty_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_routes, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(upload_routes, "GENERATED_DIR", str(tmp_path / "generated"))
    (tmp_path / "uploads").mkdir()
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def f(a: int): return a\n"))
    client.post("/api/upload", files={"file": ("c.ipynb", nbformat.writes(nb).encode(), "application/json")})
    for data in _responses("c.ipynb").values():
        assert data["inferred_background"] == [] and data["startup_warnings"] == [] and data["skipped_functions"] == []
