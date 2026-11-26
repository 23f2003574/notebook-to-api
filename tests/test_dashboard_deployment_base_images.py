"""The dashboard image's two base images can be pinned by digest, and --require-pinned insists on it."""
import subprocess
import sys
from pathlib import Path

import pytest

from backend.generator.dashboard_deployment import generate_dashboard_deployment

ROOT = Path(__file__).resolve().parent.parent
NODE = "node@sha256:" + "1a" * 32
PY = "python@sha256:" + "2b" * 32


def _froms(out):
    return [line for line in (out / "Dockerfile").read_text().splitlines() if line.startswith("FROM ")]


def test_default_images_are_unchanged(tmp_path):
    generate_dashboard_deployment(tmp_path)
    assert _froms(tmp_path) == ["FROM node:22-slim AS frontend", "FROM python:3.13-slim"]


def test_both_stages_use_the_selected_digests(tmp_path):
    generate_dashboard_deployment(tmp_path, node_image=NODE, python_image=PY, require_pinned=True)
    assert _froms(tmp_path) == [f"FROM {NODE} AS frontend", f"FROM {PY}"]


def test_require_pinned_names_every_floating_image_and_writes_nothing(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(ValueError, match=r"node:22-slim.*|python:3\.13-slim"):
        generate_dashboard_deployment(out, node_image=None, python_image=PY, require_pinned=True)
    assert not out.exists()
    with pytest.raises(ValueError, match="python:3.12-slim"):
        generate_dashboard_deployment(out, node_image=NODE, python_image="python:3.12-slim", require_pinned=True)


@pytest.mark.parametrize("bad", ["node@sha256:short", "node:22\nRUN evil", "py thon"])
def test_invalid_references_are_rejected(tmp_path, bad):
    with pytest.raises(ValueError, match="invalid base image"):
        generate_dashboard_deployment(tmp_path / "o", node_image=bad)


def _cli(tmp_path, *flags):
    return subprocess.run(
        [sys.executable, "-m", "backend.cli", "dashboard-deployment", "--output", str(tmp_path / "o"), *flags],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )


def test_cli_passes_the_images_through_and_enforces_pinning(tmp_path):
    ok = _cli(tmp_path, "--node-image", NODE, "--python-image", PY, "--require-pinned")
    assert ok.returncode == 0, ok.stderr
    assert f"FROM {PY}" in (tmp_path / "o" / "Dockerfile").read_text()
    refused = _cli(tmp_path / "x", "--require-pinned")
    assert refused.returncode == 1 and "not pinned by digest" in refused.stderr
