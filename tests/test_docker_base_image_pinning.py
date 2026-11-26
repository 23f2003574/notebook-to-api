"""The generated Dockerfile's base image can be pinned by digest, and --require-pinned insists on it."""
import json
import subprocess
import sys
from pathlib import Path

import nbformat
import pytest

from backend.generator.docker_generator import (
    base_image_is_digest_pinned, configured_base_image, dockerfile_content, validate_base_image,
)

DIGEST = "sha256:" + "ab12" * 16
PINNED = f"python@{DIGEST}"
ROOT = Path(__file__).resolve().parent.parent


def test_default_dockerfile_still_uses_the_floating_tag(monkeypatch):
    monkeypatch.delenv("NOTEBOOK_API_BASE_IMAGE", raising=False)
    assert "FROM python:3.13-slim\n" in dockerfile_content("app", "3.13")


def test_explicit_digest_reference_is_used_verbatim():
    text = dockerfile_content("app", "3.13", base_image=PINNED)
    assert f"\nFROM {PINNED}\n" in "\n" + text and "python:3.13-slim" not in text
    assert "pinned by digest" in text


def test_environment_variable_selects_the_base_image(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_BASE_IMAGE", "registry.example.com/team/python:3.13-slim")
    text = dockerfile_content("app", "3.13")
    assert "FROM registry.example.com/team/python:3.13-slim\n" in text and "pinned by digest" not in text


@pytest.mark.parametrize("ref", [
    "python@sha256:abc", f"python@sha256:{'A' * 64}", f"python@sha512:{'a' * 64}", "py thon:3", "python:3\nRUN evil",
    "python:3 AS x", "@sha256:" + "a" * 64, "",
])
def test_invalid_references_are_rejected(ref):
    with pytest.raises(ValueError):
        validate_base_image(ref)


def test_digest_pinned_detection():
    assert base_image_is_digest_pinned(PINNED)
    assert base_image_is_digest_pinned(f"python:3.13-slim@{DIGEST}")
    assert not base_image_is_digest_pinned("python:3.13-slim")
    assert not base_image_is_digest_pinned("python@sha256:short")


def test_blank_environment_value_means_unset():
    assert configured_base_image({"NOTEBOOK_API_BASE_IMAGE": "  "}) is None


def _compile(tmp_path, *flags):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def add(a: int, b: int) -> int:\n    return a + b\n"))
    path = tmp_path / "n.ipynb"
    nbformat.write(nb, path)
    out = tmp_path / "out"
    env = {k: v for k, v in __import__("os").environ.items() if k != "NOTEBOOK_API_BASE_IMAGE"}
    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", "compile", str(path), "--output", str(out), *flags],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    return proc, out


def test_compile_writes_the_pinned_base_image(tmp_path):
    proc, out = _compile(tmp_path, "--base-image", PINNED)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert f"FROM {PINNED}\n" in (out / "Dockerfile").read_text()


def test_require_pinned_refuses_a_floating_tag_and_the_default(tmp_path):
    for flags in (("--require-pinned", "--base-image", "python:3.13-slim"), ("--require-pinned",)):
        proc, out = _compile(tmp_path, *flags)
        assert proc.returncode == 1 and "not pinned by digest" in proc.stderr
        assert not (out / "Dockerfile").exists()


def test_require_pinned_accepts_a_digest_reference(tmp_path):
    proc, out = _compile(tmp_path, "--require-pinned", "--base-image", PINNED)
    # requirements may be unpinned here (nothing is installed), but the base image check passed
    assert "base image is not pinned" not in proc.stderr
    assert f"FROM {PINNED}\n" in (out / "Dockerfile").read_text()


def test_invalid_base_image_is_rejected_before_compiling(tmp_path):
    proc, out = _compile(tmp_path, "--base-image", "python@sha256:xyz")
    assert proc.returncode == 1 and "invalid base image" in proc.stderr
    assert not out.exists() or not (out / "Dockerfile").exists()
