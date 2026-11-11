import os

import nbformat
import pytest

from backend import compiler
from backend.compiler import compile_notebook_to_api

DEPLOY = ("Dockerfile", ".dockerignore", "docker-compose.yml", ".env.example", "kubernetes.yaml", "README.md")


def _nb(tmp_path, body="def add(a: int, b: int) -> int:\n    \"add\"\n    return a + b\n"):
    path = tmp_path / "nb.ipynb"
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(body)]), path)
    return str(path)


def _snapshot(out):
    return {n: (out / n).read_bytes() for n in DEPLOY}


def test_success_publishes_all_artifacts_and_leaves_no_staging(tmp_path):
    out = tmp_path / "out"
    compile_notebook_to_api(_nb(tmp_path), str(out / "app.py"))
    assert all((out / n).is_file() for n in DEPLOY)
    assert not [n for n in os.listdir(out) if n.startswith(compiler._DEPLOY_STAGING_PREFIX)]


def test_late_generation_failure_keeps_previous_deployment_files(tmp_path, monkeypatch):
    out = tmp_path / "out"
    nb = _nb(tmp_path)
    compile_notebook_to_api(nb, str(out / "app.py"))
    before = _snapshot(out)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(compiler, "generate_readme", boom)
    with pytest.raises(OSError):
        compile_notebook_to_api(_nb(tmp_path, "def mul(a: int, b: int) -> int:\n    \"m\"\n    return a * b\n"),
                                str(out / "app.py"))
    assert _snapshot(out) == before
    assert not [n for n in os.listdir(out) if n.startswith(compiler._DEPLOY_STAGING_PREFIX)]


def test_invalid_staged_manifest_is_not_published(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    out = tmp_path / "out"
    compile_notebook_to_api(_nb(tmp_path), str(out / "app.py"))
    before = _snapshot(out)
    monkeypatch.setattr(compiler, "generate_kubernetes_manifest",
                        lambda path, *a, **k: open(path, "w").write("kind: Deployment\n"))
    with pytest.raises(ValueError, match="apiVersion"):
        compile_notebook_to_api(_nb(tmp_path), str(out / "app.py"))
    assert _snapshot(out) == before


from backend.generator.manifest_validator import ManifestValidationError, verify_deployment_artifacts


def _staged(tmp_path):
    out = tmp_path / "out"
    compile_notebook_to_api(_nb(tmp_path), str(out / "app.py"))
    stage = tmp_path / "stage"
    stage.mkdir()
    for n in DEPLOY:
        (stage / n).write_bytes((out / n).read_bytes())
    return stage, out


def test_verify_accepts_complete_artifacts(tmp_path):
    verify_deployment_artifacts(*map(str, _staged(tmp_path)))


@pytest.mark.parametrize("name", DEPLOY)
def test_verify_rejects_missing_or_empty_artifact(tmp_path, name):
    stage, out = _staged(tmp_path)
    (stage / name).write_text("  \n")
    with pytest.raises(ManifestValidationError, match=f"{name}: .*empty"):
        verify_deployment_artifacts(str(stage), str(out))
    (stage / name).unlink()
    with pytest.raises(ManifestValidationError, match=f"{name}: .*not generated"):
        verify_deployment_artifacts(str(stage), str(out))


def test_verify_rejects_incomplete_dockerfile_and_dangling_copy(tmp_path):
    stage, out = _staged(tmp_path)
    text = (stage / "Dockerfile").read_text()
    (stage / "Dockerfile").write_text("\n".join(l for l in text.splitlines() if not l.startswith("CMD")))
    with pytest.raises(ManifestValidationError, match="missing CMD"):
        verify_deployment_artifacts(str(stage), str(out))
    (stage / "Dockerfile").write_text(text)
    (out / "requirements.txt").unlink()
    with pytest.raises(ManifestValidationError, match="requirements.txt"):
        verify_deployment_artifacts(str(stage), str(out))


def test_compile_publishes_nothing_when_an_artifact_is_empty(tmp_path, monkeypatch):
    out = tmp_path / "out"
    compile_notebook_to_api(_nb(tmp_path), str(out / "app.py"))
    before = _snapshot(out)
    monkeypatch.setattr(compiler, "generate_dockerignore", lambda path: open(path, "w").close())
    with pytest.raises(ValueError, match=r"\.dockerignore: .*empty"):
        compile_notebook_to_api(_nb(tmp_path), str(out / "app.py"))
    assert _snapshot(out) == before


def test_verify_rejects_compose_variable_missing_from_env_example(tmp_path):
    stage, out = _staged(tmp_path)
    text = (stage / ".env.example").read_text()
    (stage / ".env.example").write_text(text.replace("NOTEBOOK_API_MAX_TASKS=", "# removed="))
    with pytest.raises(ManifestValidationError, match="NOTEBOOK_API_MAX_TASKS"):
        verify_deployment_artifacts(str(stage), str(out))


def test_failed_publish_move_restores_previously_published_files(tmp_path, monkeypatch):
    out = tmp_path / "out"
    nb = _nb(tmp_path)
    compile_notebook_to_api(nb, str(out / "app.py"))
    before = _snapshot(out)
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if os.path.basename(dst) == "kubernetes.yaml" and ".deploy-staging-" in src:
            raise OSError("device busy")
        return real(src, dst)

    monkeypatch.setattr(compiler.os, "replace", flaky)
    with pytest.raises(OSError, match="device busy"):
        compile_notebook_to_api(_nb(tmp_path, "def mul(a: int, b: int) -> int:\n    \"m\"\n    return a * b\n"),
                                str(out / "app.py"))
    monkeypatch.undo()
    assert os.path.basename(calls[-1]) in DEPLOY  # the failure hit the publish step
    assert _snapshot(out) == before
