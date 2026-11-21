"""remote-audit against a live dashboard (real process, real SQLite audit trail, real key)."""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import nbformat
import pytest

ROOT = Path(__file__).resolve().parent.parent
KEY = "live-dashboard-key-0123456789"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def dashboard(tmp_path_factory):
    work = tmp_path_factory.mktemp("remote_audit")
    port = _free_port()
    env = {
        **os.environ, "PYTHONPATH": str(ROOT), "NOTEBOOK_API_DASHBOARD_HOST": "127.0.0.1",
        "NOTEBOOK_API_DASHBOARD_PORT": str(port), "NOTEBOOK_API_DASHBOARD_KEY": KEY,
        "NOTEBOOK_API_UPLOAD_DIR": str(work / "up"), "NOTEBOOK_API_GENERATED_DIR": str(work / "gen"),
        "NOTEBOOK_API_GENERATED_APPS_DIR": str(work / "apps"),
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND": "sqlite",
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH": str(work / "audit.db"),
        "NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT": "1",
    }
    env.pop("NOTEBOOK2API_ENVIRONMENT", None)
    proc = subprocess.Popen([sys.executable, "-m", "backend.dashboard"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(300):
        try:
            if httpx.get(f"{url}/api/health", headers={"X-API-Key": KEY}, timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("dashboard did not start")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def f(a: int):\n    return a\n"))
    (work / "n.ipynb").write_text(nbformat.writes(nb))
    yield {"url": url, "work": work, "db": work / "audit.db"}
    proc.kill()
    proc.wait(30)


def _cli(dashboard, *args, key=KEY, url=None):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("NOTEBOOK_API_DASHBOARD_KEY_FILE", None)
    if key:
        env["NOTEBOOK_API_DASHBOARD_KEY"] = key
    else:
        env.pop("NOTEBOOK_API_DASHBOARD_KEY", None)
    proc = subprocess.run(
        [sys.executable, "-m", "backend.cli", *args, "--dashboard-url", url or dashboard["url"]],
        capture_output=True, text=True, timeout=120, cwd=ROOT, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def _api(dashboard, path, **kw):
    return httpx.get(dashboard["url"] + path, headers={"X-API-Key": KEY}, timeout=30, **kw).json()


def test_compile_prints_the_audit_record_and_list_show_match_the_api(dashboard):
    code, out, err = _cli(dashboard, "upload", str(dashboard["work"] / "n.ipynb"))
    assert code == 0, out + err
    code, out, err = _cli(dashboard, "remote-compile", "n.ipynb")
    assert code == 0, out + err
    line = next(l for l in out.splitlines() if l.startswith("Governance audit record: #"))
    sequence = int(line.split("#")[1].split()[0])

    code, out, _ = _cli(dashboard, "remote-audit", "list", "--notebook", "n.ipynb", "--json")
    listed = json.loads(out)
    assert code == 0 and listed == _api(dashboard, "/governance/audit/history?notebook=n.ipynb&limit=25")
    assert any(r["sequence"] == sequence for r in listed["records"])

    code, out, _ = _cli(dashboard, "remote-audit", "show", str(sequence), "--json")
    assert code == 0 and json.loads(out) == _api(dashboard, f"/governance/audit/{sequence}")
    code, out, _ = _cli(dashboard, "remote-audit", "show", str(sequence))
    assert "notebook.compile" in out and "record hash" in out
    code, _, err = _cli(dashboard, "remote-audit", "show", "999999")
    assert code == 1 and "no audit record" in err


def test_list_filters_and_cursor_pagination(dashboard):
    for _ in range(3):
        _cli(dashboard, "remote-compile", "n.ipynb")
    code, out, _ = _cli(dashboard, "remote-audit", "list", "--action", "notebook.compile", "--outcome", "success",
                        "--limit", "2", "--json")
    page = json.loads(out)
    assert code == 0 and len(page["records"]) == 2 and page["has_more"]
    code, out, _ = _cli(dashboard, "remote-audit", "list", "--action", "notebook.compile", "--outcome", "success",
                        "--limit", "2", "--cursor", page["next_cursor"], "--json")
    second = json.loads(out)
    assert {r["sequence"] for r in page["records"]}.isdisjoint({r["sequence"] for r in second["records"]})
    code, out, _ = _cli(dashboard, "remote-audit", "list", "--outcome", "failure", "--notebook", "never.ipynb")
    assert code == 0 and "No audit records match" in out
    code, out, _ = _cli(dashboard, "remote-audit", "list", "--limit", "1")
    assert "More records: add --cursor" in out


def test_verify_exit_codes_intact_broken_and_unavailable(dashboard):
    code, out, _ = _cli(dashboard, "remote-audit", "verify")
    assert code == 0 and "Chain intact" in out and "unkeyed" in out
    code, out, _ = _cli(dashboard, "remote-audit", "verify", "--json")
    assert code == 0 and json.loads(out)["status"] == "intact"

    # unavailable: wrong credentials and an unreachable dashboard are not "broken"
    code, _, err = _cli(dashboard, "remote-audit", "verify", key="wrong-key")
    assert code == 4 and "unavailable" in err and "does not mean the chain is broken" in err
    code, out, _ = _cli(dashboard, "remote-audit", "verify", "--json", url="http://127.0.0.1:1")
    assert code == 4 and json.loads(out)["status"] == "unavailable"

    # broken: tamper with a stored record directly in the database
    conn = sqlite3.connect(dashboard["db"])
    conn.execute("UPDATE audit_records SET actor = 'evil' WHERE sequence = 1")
    conn.commit()
    conn.close()
    code, out, _ = _cli(dashboard, "remote-audit", "verify")
    assert code == 3 and "BROKEN at record #1" in out
    code, out, _ = _cli(dashboard, "remote-audit", "verify", "--json")
    assert code == 3 and json.loads(out)["status"] == "broken"


def test_the_key_file_is_supported_and_the_key_only_goes_to_the_dashboard(dashboard, tmp_path):
    from backend.cli_remote_audit import dashboard_key, install_dashboard_credentials

    keyfile = tmp_path / "key"
    keyfile.write_text(KEY + "\n")
    old = {k: os.environ.pop(k, None) for k in ("NOTEBOOK_API_DASHBOARD_KEY", "NOTEBOOK_API_DASHBOARD_KEY_FILE")}
    os.environ["NOTEBOOK_API_DASHBOARD_KEY_FILE"] = str(keyfile)
    try:
        assert dashboard_key() == KEY
        seen = []

        class Fake:
            @staticmethod
            def get(url, **kw):
                seen.append((url, kw.get("headers")))

        Fake.post = Fake.put = Fake.patch = Fake.delete = Fake.request = Fake.stream = staticmethod(lambda *a, **k: None)
        assert install_dashboard_credentials(Fake, "http://dash.example:8001")
        Fake.get("http://dash.example:8001/api/list")
        Fake.get("http://other.example/api/list")
        Fake.get("http://dash.example:8001/api/x", headers={"X-API-Key": "explicit"})
        assert seen[0][1] == {"X-API-Key": KEY}
        assert seen[1][1] is None  # never sent to another host
        assert seen[2][1] == {"X-API-Key": "explicit"}
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
