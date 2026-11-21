"""compile --resolve-versions: reproducible, opt-in, offline-safe (no PyPI access needed)."""
import json
import subprocess
import sys
from pathlib import Path

import nbformat
import pytest

from backend import compiler
from backend.compiler import VersionResolutionError, compile_notebook, resolving_versions, unpinned_requirements

ROOT = Path(__file__).resolve().parent.parent
NOT_INSTALLED = "zzz_notinstalled_pkg"


def _notebook(tmp_path, imports):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("\n".join(f"import {name}" for name in imports)
                                              + "\nimport json, os\ndef f(a: int) -> int:\n    return a\n"))
    path = tmp_path / "nb.ipynb"
    path.write_text(nbformat.writes(nb))
    return path


@pytest.fixture(autouse=True)
def _no_installed_match(monkeypatch):
    # the notebook's imports are "not installed here": no version is known without resolution
    monkeypatch.setattr(compiler, "_installed_version", lambda name: None, raising=False)


def _requirements(out):
    return (out / "requirements.txt").read_text().splitlines()


def _fake(table, calls=None):
    def resolver(requirements):
        if calls is not None:
            calls.append(list(requirements))
        return {compiler._normalized_distribution_name(k): v for k, v in table.items()}
    return resolver


def test_without_the_flag_nothing_changes_and_no_resolver_runs(tmp_path):
    nb = _notebook(tmp_path, ["pandas_stub_lib"])
    out = tmp_path / "out"
    compile_notebook(str(nb), str(out))
    assert "requirements_resolution" not in json.loads((out / ".compile_metadata.json").read_text())


def test_unpinned_libraries_are_pinned_and_metadata_records_the_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler, "extract_third_party_imports", lambda cells, path: ["n2a-fake-one", "n2a-fake-two"])
    nb = _notebook(tmp_path, [])
    out = tmp_path / "out"
    calls = []
    with resolving_versions(_fake({"n2a-fake-one": "2.1.0", "n2a-fake-two": "1.5.2"}, calls)):
        compile_notebook(str(nb), str(out))
    lines = _requirements(out)
    assert "n2a-fake-one==2.1.0" in lines and "n2a-fake-two==1.5.2" in lines
    assert unpinned_requirements(str(out / "requirements.txt")) == []
    assert sorted(calls[0]) == ["n2a-fake-one", "n2a-fake-two"]
    meta = json.loads((out / ".compile_metadata.json").read_text())["requirements_resolution"]
    assert meta["status"] == "resolved" and meta["pinned"] == {"n2a-fake-one": "2.1.0", "n2a-fake-two": "1.5.2"}
    assert meta["resolved_at"] and meta["python"] == "%d.%d" % sys.version_info[:2]


def test_identical_resolver_results_give_byte_identical_requirements(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler, "extract_third_party_imports", lambda cells, path: ["n2a-fake-one", "n2a-fake-three"])
    nb = _notebook(tmp_path, [])
    outputs = []
    for name in ("one", "two"):
        out = tmp_path / name
        with resolving_versions(_fake({"n2a-fake-one": "2.1.0", "n2a-fake-three": "2.2.3"})):
            compile_notebook(str(nb), str(out))
        outputs.append((out / "requirements.txt").read_bytes())
    assert outputs[0] == outputs[1]


def test_offline_resolution_warns_and_still_compiles_unpinned(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(compiler, "extract_third_party_imports", lambda cells, path: ["n2a-fake-one"])
    nb = _notebook(tmp_path, [])
    out = tmp_path / "out"

    def offline(requirements):
        raise VersionResolutionError("Could not connect to the package index")

    with resolving_versions(offline):
        compile_notebook(str(nb), str(out))
    assert "n2a-fake-one" in _requirements(out) and unpinned_requirements(str(out / "requirements.txt")) == ["n2a-fake-one"]
    printed = capsys.readouterr().out
    assert "Could not resolve versions (Could not connect to the package index)" in printed
    meta = json.loads((out / ".compile_metadata.json").read_text())["requirements_resolution"]
    assert meta["status"] == "unresolved" and meta["pinned"] == {}


def test_explicit_and_already_pinned_requirements_and_unknown_names_are_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(compiler, "extract_third_party_imports", lambda cells, path: ["n2a-fake-one", "mystery"])
    nb = _notebook(tmp_path, [])
    nb_cells = nbformat.read(str(nb), as_version=4)
    nb_cells.cells[0].source = "# notebook-to-api: requires n2a-fake-explicit==2.0.0\n" + nb_cells.cells[0].source
    nbformat.write(nb_cells, str(nb))
    out = tmp_path / "out"
    calls = []
    with resolving_versions(_fake({"n2a-fake-one": "2.1.0"}, calls)):
        compile_notebook(str(nb), str(out))
    lines = _requirements(out)
    assert "n2a-fake-explicit==2.0.0" in lines and "n2a-fake-one==2.1.0" in lines and "mystery" in lines
    assert all("n2a-fake-three" not in c for call in calls for c in call)  # explicit pins are never re-resolved
    meta = json.loads((out / ".compile_metadata.json").read_text())["requirements_resolution"]
    assert meta["status"] == "partial" and meta["unresolved"] == ["mystery"]


def test_local_modules_and_the_standard_library_are_never_resolved(tmp_path):
    (tmp_path / "helper.py").write_text("X = 1\n")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("import helper, json, os\ndef f(a: int) -> int:\n    return a\n"))
    path = tmp_path / "nb.ipynb"
    path.write_text(nbformat.writes(nb))
    calls = []
    with resolving_versions(_fake({}, calls)):
        compile_notebook(str(path), str(tmp_path / "out"))
    assert all("helper" not in c and "json" not in c for call in calls for c in call)


def test_the_pip_resolver_parses_pips_report(tmp_path, monkeypatch):
    report = {"install": [
        {"requested": True, "metadata": {"name": "Scikit_Learn", "version": "1.5.2"}},
        {"requested": False, "metadata": {"name": "joblib", "version": "1.4.2"}},
    ]}

    class Done:
        returncode, stderr = 0, ""

    def fake_run(command, **kwargs):
        Path(command[command.index("--report") + 1]).write_text(json.dumps(report))
        assert "--dry-run" in command and command[1:3] == ["-m", "pip"]
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert compiler.pip_dry_run_resolver(["scikit-learn"]) == {"scikit-learn": "1.5.2"}

    class Failed:
        returncode, stderr = 1, "ERROR: No matching distribution found for nope\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(VersionResolutionError, match="No matching distribution"):
        compiler.pip_dry_run_resolver(["nope"])

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("pip", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(VersionResolutionError):
        compiler.pip_dry_run_resolver(["x"])


def test_the_cli_flag_is_wired_and_offline_degrades_gracefully(tmp_path):
    nb = _notebook(tmp_path, [])
    env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin", "PIP_INDEX_URL": "http://127.0.0.1:9/simple",
           "PIP_RETRIES": "0", "PIP_TIMEOUT": "1"}
    import os

    env = {**os.environ, **env}
    proc = subprocess.run([sys.executable, "-m", "backend.cli", "compile", str(nb), "--output", str(tmp_path / "o"),
                           "--resolve-versions"], capture_output=True, text=True, cwd=ROOT, env=env, timeout=240)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    help_text = subprocess.run([sys.executable, "-m", "backend.cli", "compile", "--help"], capture_output=True,
                               text=True, cwd=ROOT, env=env, timeout=60).stdout
    assert "--resolve-versions" in help_text
