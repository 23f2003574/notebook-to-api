"""Generated SDK packages are typed (PEP 561) and the TypeScript client can cancel requests."""
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import nbformat
import pytest

from backend.exporters.sdk_generator import generate_sdk_package

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def openapi(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("typing")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(
        "from dataclasses import dataclass\n@dataclass\nclass Point:\n    x: float\n    y: float\n"
        "def add(a: int, b: int) -> int:\n    return a + b\n"
        "def origin() -> Point:\n    return Point(0, 0)\n"
    ))
    nbformat.write(nb, tmp / "nb.ipynb")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    for args in (["compile", str(tmp / "nb.ipynb"), "--output", str(tmp / "typedsdkapp")],
                 ["export-openapi", "--app-dir", str(tmp / "typedsdkapp")]):
        done = subprocess.run([sys.executable, "-m", "backend.cli", *args], capture_output=True, text=True, env=env, cwd=ROOT)
        assert done.returncode == 0, done.stderr
    return tmp / "typedsdkapp" / "openapi.json"


def test_python_package_ships_py_typed_in_its_metadata_and_layout(openapi, tmp_path):
    files = generate_sdk_package(str(openapi), str(tmp_path / "py"), "python", package_name="typed-sdk")
    assert "typed_sdk/py.typed" in files and (tmp_path / "py" / "typed_sdk" / "py.typed").is_file()
    pyproject = (tmp_path / "py" / "pyproject.toml").read_text()
    assert '[tool.setuptools.package-data]\ntyped_sdk = ["py.typed"]' in pyproject


def _install(pkg, target):
    done = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "--quiet",
                           "--target", str(target), str(pkg)], capture_output=True, text=True)
    if done.returncode != 0 and ("No module named pip" in done.stderr or "setuptools" in done.stderr):
        pytest.skip("cannot build the package here (pip/setuptools unavailable)")
    assert done.returncode == 0, done.stderr


def test_installed_distribution_contains_py_typed(openapi, tmp_path):
    pytest.importorskip("setuptools")
    generate_sdk_package(str(openapi), str(tmp_path / "py"), "python", package_name="typed-sdk")
    _install(tmp_path / "py", tmp_path / "site")
    assert (tmp_path / "site" / "typed_sdk" / "py.typed").is_file()


def test_type_checker_sees_the_clients_types_and_catches_a_mistake(openapi, tmp_path):
    pytest.importorskip("mypy")
    pytest.importorskip("setuptools")
    import venv

    generate_sdk_package(str(openapi), str(tmp_path / "py"), "python", package_name="typed-sdk")
    env_dir = tmp_path / "consumer-env"
    venv.create(env_dir, with_pip=False)  # a consumer environment: the SDK is a *library* in its site-packages
    site = next((env_dir / "lib").glob("python*/site-packages"), None) or env_dir / "Lib" / "site-packages"
    _install(tmp_path / "py", site)
    python = env_dir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")

    runs = []

    def check(source):
        # A fresh file name and no incremental cache: two same-size sources written within one
        # second would otherwise be answered from mypy's cache.
        script = tmp_path / f"consumer_{len(runs)}.py"
        runs.append(script)
        script.write_text(source)
        return subprocess.run(
            [sys.executable, "-m", "mypy", "--no-incremental", "--python-executable", str(python), str(script)],
            capture_output=True, text=True,
        )

    prefix = "from typed_sdk import NotebookAPIClient\nc = NotebookAPIClient('http://x')\nr = c.add({'a': 1, 'b': 2})\n"
    ok = check(prefix + "value: int = r['result']\n")
    assert ok.returncode == 0, ok.stdout
    bad = check(prefix + "value: str = r['result']\n")
    assert bad.returncode != 0 and "incompatible" in bad.stdout.lower()
    (site / "typed_sdk" / "py.typed").unlink()
    untyped = check(prefix + "value: str = r['result']\n")  # no marker: mypy ignores the package's types
    assert "incompatible" not in untyped.stdout.lower()


NODE = shutil.which("node")
TSC = shutil.which("tsc")


@pytest.mark.skipif(not (NODE and TSC), reason="needs node and tsc")
def test_typescript_requests_can_be_aborted_without_retries_and_ordinary_calls_still_work(openapi, tmp_path):
    pkg = tmp_path / "ts"
    generate_sdk_package(str(openapi), str(pkg), "typescript", package_name="cancel-sdk")
    built = subprocess.run([TSC, "-p", "tsconfig.json"], cwd=pkg, capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr

    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            hits.append(self.path)
            if self.path == "/add" and len(hits) > 1:
                time.sleep(3)  # the slow call the test aborts
            body = json.dumps({"result": 3}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    script = tmp_path / "run.mjs"
    script.write_text(textwrap.dedent(f"""
        import {{ NotebookAPIClient }} from {json.dumps((pkg / "dist" / "index.js").as_uri())};
        const client = new NotebookAPIClient("http://127.0.0.1:{server.server_port}", {{ apiKey: "k", maxRetries: 3, backoffFactor: 0.01 }});
        const first = await client.add({{ a: 1, b: 2 }});
        const controller = new AbortController();
        setTimeout(() => controller.abort(), 150);
        const started = Date.now();
        let aborted = false;
        try {{ await client.add({{ a: 1, b: 2 }}, {{ signal: controller.signal }}); }} catch (e) {{ aborted = true; }}
        const pre = new AbortController(); pre.abort();
        let preAborted = false;
        try {{ await client.add({{ a: 1, b: 2 }}, {{ signal: pre.signal }}); }} catch (e) {{ preAborted = true; }}
        const after = await client.origin({{}});
        console.log(JSON.stringify({{ first, aborted, elapsed: Date.now() - started, preAborted, after }}));
    """))
    try:
        done = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["first"] == {"result": 3} and out["after"] == {"result": 3}
    assert out["aborted"] is True and out["preAborted"] is True
    assert out["elapsed"] < 2500  # cancelled long before the 3s response
    assert hits.count("/add") == 2  # the aborted call was sent once and never retried; the pre-aborted one never left
