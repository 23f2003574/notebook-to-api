"""Background endpoints are inferred from whole name tokens, and every
inferred one is reported by compile and validate."""
import json
import subprocess
import sys

import nbformat
import pytest

from backend.generator.api_generator import infer_background_by_name, resolve_is_background


@pytest.mark.parametrize("name", ["preprocess_text", "is_trained", "generate_id", "generate_uuid",
                                  "get_processed_count", "postprocessor", "untrained", "degenerate"])
def test_fast_helpers_are_not_background(name):
    assert not resolve_is_background(name)


@pytest.mark.parametrize("name", ["train_model", "process_batch", "generate_report", "embed_documents",
                                  "scrape_site", "trainModel", "run_training", "embeddings_build", "batch_process"])
def test_genuine_long_running_names_are_background(name):
    assert infer_background_by_name(name)


def test_directives_win():
    assert resolve_is_background("preprocess_text", {"preprocess_text": True})
    assert not resolve_is_background("train_model", {"train_model": False})


def _notebook(tmp_path, source):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(source))
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, path)
    return str(path)


SOURCE = (
    "def train_model(n: int = 1):\n    return n\n"
    "# notebook-to-api: background\ndef slow_thing(n: int = 1):\n    return n\n"
    "def preprocess_text(t: str):\n    return t\n"
)


def test_validate_reports_only_inferred(tmp_path):
    nb = _notebook(tmp_path, SOURCE)
    out = subprocess.run([sys.executable, "-m", "backend.cli", "validate", nb, "--json"],
                         capture_output=True, text=True)
    data = json.loads(out.stdout)
    assert data["inferred_background"] == ["train_model"]
    text = subprocess.run([sys.executable, "-m", "backend.cli", "validate", nb],
                          capture_output=True, text=True).stdout
    assert "train_model is a background endpoint inferred from its name" in text
    assert "slow_thing is a background" not in text


def test_compile_reports_inferred(tmp_path):
    nb = _notebook(tmp_path, SOURCE)
    out = subprocess.run([sys.executable, "-m", "backend.cli", "compile", nb, "--output", str(tmp_path / "app")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "Background endpoints inferred from function names" in out.stdout
    assert "train_model is a background endpoint" in out.stdout
    assert "preprocess_text is a background" not in out.stdout
