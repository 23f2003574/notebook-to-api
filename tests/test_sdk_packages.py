"""export-sdk --package writes installable Python and TypeScript packages."""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import nbformat
import pytest

from backend.exporters.sdk_generator import generate_sdk_package

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def openapi(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pkg")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def add(a: int, b: int) -> int:\n    return a + b\n"))
    nbpath = tmp / "nb.ipynb"
    nbformat.write(nb, nbpath)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    app = tmp / "myapp"
    for args in (["compile", str(nbpath), "--output", str(app)], ["export-openapi", "--app-dir", str(app)]):
        done = subprocess.run([sys.executable, "-m", "backend.cli", *args], capture_output=True, text=True, env=env, cwd=ROOT)
        assert done.returncode == 0, done.stderr
    return app / "openapi.json"


def test_python_package_layout_and_declared_dependency(openapi, tmp_path):
    files = generate_sdk_package(str(openapi), str(tmp_path / "py"), "python", package_name="My Client")
    assert set(files) == {"my_client/client.py", "my_client/__init__.py", "my_client/py.typed", "pyproject.toml", "README.md"}
    pyproject = (tmp_path / "py" / "pyproject.toml").read_text()
    assert 'name = "my-client"' in pyproject and '"requests>=2.28"' in pyproject


def test_python_package_installs_and_calls_an_api(openapi, tmp_path):
    pytest.importorskip("requests")
    pytest.importorskip("setuptools")
    pkg = tmp_path / "py"
    generate_sdk_package(str(openapi), str(pkg), "python", package_name="demo-sdk")
    target = tmp_path / "site"
    done = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "--quiet",
                           "--target", str(target), str(pkg)], capture_output=True, text=True)
    if done.returncode != 0 and "No module named pip" in done.stderr:
        pytest.skip("pip unavailable")
    assert done.returncode == 0, done.stderr
    metadata = next(target.glob("demo_sdk-*.dist-info")) / "METADATA"
    assert "Requires-Dist: requests>=2.28" in metadata.read_text()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"status": "healthy"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        code = (f"import sys; sys.path.insert(0, {str(target)!r})\n"
                "from demo_sdk import NotebookAPIClient\n"
                f"print(NotebookAPIClient('http://127.0.0.1:{server.server_port}', api_key='k').health())")
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                             cwd=tmp_path)
        assert out.returncode == 0, out.stderr
        assert "healthy" in out.stdout
    finally:
        server.shutdown()


def test_typescript_package_layout(openapi, tmp_path):
    files = generate_sdk_package(str(openapi), str(tmp_path / "ts"), "typescript", package_name="demo-sdk")
    assert set(files) == {"src/index.ts", "package.json", "tsconfig.json", "README.md"}
    manifest = json.loads((tmp_path / "ts" / "package.json").read_text())
    assert manifest["name"] == "demo-sdk" and manifest["types"] == "dist/index.d.ts"
    assert manifest["dependencies"] == {} and manifest["scripts"]["build"]


@pytest.mark.skipif(shutil.which("tsc") is None, reason="tsc not installed")
def test_typescript_package_builds_with_its_own_tsconfig(openapi, tmp_path):
    generate_sdk_package(str(openapi), str(tmp_path / "ts"), "typescript")
    done = subprocess.run(["tsc", "-p", "tsconfig.json"], cwd=tmp_path / "ts", capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (tmp_path / "ts" / "dist" / "index.js").is_file() and (tmp_path / "ts" / "dist" / "index.d.ts").is_file()


def test_cli_package_flag_and_loose_export_still_work(openapi, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    app_dir = str(openapi.parent)
    run = lambda *a: subprocess.run([sys.executable, "-m", "backend.cli", "export-sdk", "--app-dir", app_dir, *a],  # noqa: E731
                                    capture_output=True, text=True, env=env, cwd=ROOT)
    packaged = run("--language", "typescript", "--package", "--output", str(tmp_path / "pk"), "--json")
    assert packaged.returncode == 0, packaged.stderr
    assert "package.json" in json.loads(packaged.stdout[packaged.stdout.index("{"):])["files"]
    loose = run("--language", "python", "--output", str(tmp_path / "loose.py"))
    assert loose.returncode == 0 and (tmp_path / "loose.py").is_file()


def test_invalid_language_is_rejected(openapi, tmp_path):
    with pytest.raises(ValueError):
        generate_sdk_package(str(openapi), str(tmp_path / "x"), "ruby")
