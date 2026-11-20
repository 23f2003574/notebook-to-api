"""Generated SDK packages build and pack into complete, installable distributions."""
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import nbformat
import pytest

from backend.exporters.sdk_generator import generate_sdk_package

ROOT = Path(__file__).resolve().parents[1]
NODE, NPM, TSC = shutil.which("node"), shutil.which("npm"), shutil.which("tsc")


@pytest.fixture(scope="module")
def openapi(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("publish")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def add(a: int, b: int) -> int:\n    return a + b\n"))
    nbformat.write(nb, tmp / "nb.ipynb")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    for args in (["compile", str(tmp / "nb.ipynb"), "--output", str(tmp / "pubapp")], ["export-openapi", "--app-dir", str(tmp / "pubapp")]):
        done = subprocess.run([sys.executable, "-m", "backend.cli", *args], capture_output=True, text=True, env=env, cwd=ROOT)
        assert done.returncode == 0, done.stderr
    return tmp / "pubapp" / "openapi.json"


def test_typescript_manifest_builds_before_packing_and_declares_its_runtime(openapi, tmp_path):
    generate_sdk_package(str(openapi), str(tmp_path / "ts"), "typescript", package_name="pub-sdk")
    manifest = json.loads((tmp_path / "ts" / "package.json").read_text())
    assert manifest["scripts"]["prepack"] == "npm run build" and manifest["scripts"]["prepare"] == "npm run build"
    assert manifest["files"] == ["dist"] and manifest["main"] == "dist/index.js" and manifest["types"] == "dist/index.d.ts"
    assert manifest["exports"]["."] == {"types": "./dist/index.d.ts", "default": "./dist/index.js"}
    assert manifest["engines"] == {"node": ">=18"} and manifest["dependencies"] == {}
    assert "dist/" in (tmp_path / "ts" / ".gitignore").read_text()


@pytest.mark.skipif(not (NODE and NPM and TSC), reason="needs node, npm and tsc")
def test_npm_pack_contains_the_compiled_client_and_the_tarball_installs_and_imports(openapi, tmp_path):
    pkg = tmp_path / "ts"
    generate_sdk_package(str(openapi), str(pkg), "typescript", package_name="pub-sdk")
    dry = subprocess.run([NPM, "pack", "--dry-run", "--json"], cwd=pkg, capture_output=True, text=True)
    assert dry.returncode == 0, dry.stderr
    listed = {entry["path"] for entry in json.loads(dry.stdout[dry.stdout.index("["):])[0]["files"]}
    assert {"dist/index.js", "dist/index.d.ts", "package.json", "README.md"} <= listed
    assert not any(name.startswith("src/") for name in listed)

    packed = subprocess.run([NPM, "pack", "--pack-destination", str(tmp_path)], cwd=pkg, capture_output=True, text=True)
    assert packed.returncode == 0, packed.stderr
    tarball = next(tmp_path.glob("pub-sdk-*.tgz"))
    with tarfile.open(tarball) as archive:
        assert "package/dist/index.js" in archive.getnames()

    project = tmp_path / "consumer"
    project.mkdir()
    (project / "package.json").write_text(json.dumps({"name": "consumer", "version": "1.0.0", "type": "module"}))
    installed = subprocess.run([NPM, "install", "--offline", "--no-audit", "--no-fund", "--ignore-scripts", str(tarball)],
                               cwd=project, capture_output=True, text=True)
    assert installed.returncode == 0, installed.stderr
    (project / "main.mjs").write_text(
        'import { NotebookAPIClient } from "pub-sdk";\n'
        'const c = new NotebookAPIClient("http://127.0.0.1:1", { apiKey: "k", maxRetries: 0 });\n'
        'console.log(typeof c.add, typeof c.health);\n'
    )
    ran = subprocess.run([NODE, "main.mjs"], cwd=project, capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0 and ran.stdout.split() == ["function", "function"], ran.stderr


@pytest.mark.skipif(not (NODE and TSC), reason="needs node and tsc")
def test_a_consumer_can_type_check_against_the_packed_declarations(openapi, tmp_path):
    pkg = tmp_path / "ts"
    generate_sdk_package(str(openapi), str(pkg), "typescript", package_name="pub-sdk")
    assert subprocess.run([TSC, "-p", "tsconfig.json"], cwd=pkg, capture_output=True, text=True).returncode == 0
    (tmp_path / "use.ts").write_text(
        f'import {{ NotebookAPIClient }} from {json.dumps(str(pkg / "dist" / "index.js"))};\n'
        'const c = new NotebookAPIClient("http://x");\n'
        'const r: Promise<{ result: number }> = c.add({ a: 1, b: 2 });\n'
        'const controller = new AbortController();\n'
        'c.add({ a: 1, b: 2 }, { signal: controller.signal });\n'
    )
    done = subprocess.run([TSC, "--noEmit", "--strict", "--target", "es2020", "--lib", "es2020,dom", "--module", "esnext",
                           "--moduleResolution", "bundler", str(tmp_path / "use.ts")], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout


def _wheel(pkg, out):
    return subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--quiet", "-w", str(out), str(pkg)],
                          capture_output=True, text=True)


def test_python_wheel_contains_the_client_the_typing_marker_and_declared_dependencies(openapi, tmp_path):
    pytest.importorskip("setuptools")
    generate_sdk_package(str(openapi), str(tmp_path / "py"), "python", package_name="pub-sdk")
    built = _wheel(tmp_path / "py", tmp_path / "dist")
    if built.returncode != 0 and ("No module named pip" in built.stderr or "bdist_wheel" in built.stderr):
        pytest.skip("cannot build wheels here")
    assert built.returncode == 0, built.stderr
    wheel = next((tmp_path / "dist").glob("pub_sdk-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = archive.read(next(n for n in names if n.endswith("METADATA"))).decode()
    assert {"pub_sdk/__init__.py", "pub_sdk/client.py", "pub_sdk/py.typed"} <= names
    assert "Requires-Dist: requests>=2.28" in metadata and "Requires-Python: >=3.9" in metadata
    assert "Classifier: Typing :: Typed" in metadata


def test_the_installed_wheel_imports_in_a_clean_environment(openapi, tmp_path):
    pytest.importorskip("setuptools")
    pytest.importorskip("requests")
    import venv

    generate_sdk_package(str(openapi), str(tmp_path / "py"), "python", package_name="pub-sdk")
    built = _wheel(tmp_path / "py", tmp_path / "dist")
    if built.returncode != 0:
        pytest.skip("cannot build wheels here")
    env_dir = tmp_path / "clean"
    venv.create(env_dir, with_pip=False)
    site = next((env_dir / "lib").glob("python*/site-packages"), None) or env_dir / "Lib" / "site-packages"
    with zipfile.ZipFile(next((tmp_path / "dist").glob("pub_sdk-*.whl"))) as archive:
        archive.extractall(site)
    python = env_dir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
    # requests is the declared dependency: borrow it from this interpreter, nothing else from the repo
    import requests

    code = ("import sys; sys.path.append(%r)\nimport pub_sdk\nprint(pub_sdk.NotebookAPIClient.__name__)" % str(Path(requests.__file__).parents[1]))
    done = subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True, cwd=tmp_path)
    assert done.returncode == 0 and "NotebookAPIClient" in done.stdout, done.stderr
