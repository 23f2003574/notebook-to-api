"""The governance audit trail across application lifecycles: restarts, retries, concurrency, loss."""
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import nbformat
import pytest
from fastapi.testclient import TestClient

import backend.routes.upload as upload_routes
from backend.dashboard import app
from backend.observability import deployment_governance_audit as audit
from backend.observability.deployment_governance_audit_store import SqliteAuditStore

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(app)


def _notebook(path, name, src="def f(x: int) -> int:\n    return x\n"):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(src))
    nbformat.write(nb, path / name)


# ---- a real dashboard process, restarted --------------------------------------------------------

class Dashboard:
    def __init__(self, work):
        self.work = work
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.proc = None

    def start(self):
        env = {
            **os.environ,
            "NOTEBOOK_API_DASHBOARD_HOST": "127.0.0.1", "NOTEBOOK_API_DASHBOARD_PORT": str(self.port),
            "NOTEBOOK_API_UPLOAD_DIR": str(self.work / "uploads"), "NOTEBOOK_API_GENERATED_DIR": str(self.work / "generated"),
            "NOTEBOOK_API_GENERATED_APPS_DIR": str(self.work / "apps"),
            "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND": "sqlite",
            "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH": str(self.work / "audit.db"), "PYTHONPATH": str(ROOT),
        }
        env.pop("NOTEBOOK2API_ENVIRONMENT", None)
        self.proc = subprocess.Popen([sys.executable, "-m", "backend.dashboard"], cwd=str(ROOT), env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if httpx.get(self.url("/api/health"), timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise AssertionError("dashboard did not start")

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=30)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def post(self, path, **kw):
        return httpx.post(self.url(path), timeout=120, **kw)

    def get(self, path, **kw):
        return httpx.get(self.url(path), timeout=60, **kw)


def test_records_survive_a_real_restart_and_the_chain_still_verifies(tmp_path):
    (tmp_path / "uploads").mkdir()
    _notebook(tmp_path / "uploads", "life.ipynb")
    dash = Dashboard(tmp_path)
    dash.start()
    try:
        ok = dash.post("/api/compile", json={"notebook_path": "life.ipynb", "per_notebook": True}, headers={"Idempotency-Key": "req-1"})
        bad = dash.post("/api/compile", json={"notebook_path": "gone.ipynb"})
        ok_seq = ok.json()["governance_audit"]["sequence"]
        bad_seq = int(bad.headers["X-Governance-Audit-Sequence"])
    finally:
        dash.stop()

    dash.start()
    try:
        located = dash.get(f"/governance/audit/{ok_seq}").json()
        assert located["action"] == "notebook.compile" and located["outcome"] == "success"
        assert dash.get(f"/governance/audit/{bad_seq}").json()["outcome"] == "failure"
        # a retry of the first request after the restart does not add a second record
        retry = dash.post("/api/compile", json={"notebook_path": "life.ipynb", "per_notebook": True}, headers={"Idempotency-Key": "req-1"})
        assert retry.json()["governance_audit"] == {**ok.json()["governance_audit"], "replayed": True}
        mine = dash.get("/governance/audit", params={"resource": "notebook:life.ipynb"}).json()
        assert len(mine) == 1
        # new work continues the sequence instead of restarting it
        later = dash.post("/api/compile", json={"notebook_path": "life.ipynb", "per_notebook": True})
        assert later.json()["governance_audit"]["sequence"] > bad_seq
        verified = dash.get("/governance/audit/verify").json()
        assert verified["valid"] is True and verified["checked"] >= 3
        assert dash.get("/governance/audit/health").json()["state"] == "healthy"
    finally:
        dash.stop()


# ---- in-process scenarios ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    for name, sub in (("UPLOAD_DIR", "uploads"), ("GENERATED_DIR", "generated"), ("GENERATED_APPS_DIR", "apps")):
        monkeypatch.setattr(upload_routes, name, str(tmp_path / sub))
    (tmp_path / "uploads").mkdir()
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(tmp_path / "audit.db"))
    audit.reset_audit_service()
    yield tmp_path
    audit.reset_audit_service()


def _compile(name, **headers):
    return client.post("/api/compile", json={"notebook_path": name, "per_notebook": True}, headers=headers)


def test_concurrent_operations_keep_the_chain_contiguous_and_valid(env):
    names = [f"c{i}.ipynb" for i in range(8)]
    for n in names:
        _notebook(env / "uploads", n)
    results = []

    def work(n):
        results.append(_compile(n).json()["governance_audit"]["sequence"])

    threads = [threading.Thread(target=work, args=(n,)) for n in names]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == list(range(min(results), min(results) + 8))
    assert client.get("/governance/audit/verify").json()["valid"] is True


def test_concurrent_retries_with_one_key_produce_one_record(env):
    _notebook(env / "uploads", "once.ipynb")
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(_compile("once.ipynb", **{"Idempotency-Key": "same-key"}).json()["governance_audit"]["sequence"])) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(answers)) == 1
    assert len(client.get("/governance/audit", params={"resource": "notebook:once.ipynb"}).json()) == 1


def test_an_idempotency_key_is_per_outcome_and_validated(env):
    _notebook(env / "uploads", "k.ipynb")
    fail = client.post("/api/compile", json={"notebook_path": "k_missing.ipynb"}, headers={"Idempotency-Key": "abc"})
    first = _compile("k.ipynb", **{"Idempotency-Key": "abc"})
    assert fail.status_code >= 400 and "governance_audit" in first.json()  # a success after a failure is its own record
    assert client.post("/api/compile", json={"notebook_path": "k.ipynb"}, headers={"Idempotency-Key": "bad key!"}).status_code == 400


def test_an_audit_store_failure_never_changes_the_operations_own_answer(env):
    _notebook(env / "uploads", "sem.ipynb")
    healthy_fail = client.post("/api/compile", json={"notebook_path": "nope.ipynb"})
    service = audit.get_audit_service()
    service._store.append = lambda build: (_ for _ in ()).throw(RuntimeError("disk full"))
    down_fail = client.post("/api/compile", json={"notebook_path": "nope.ipynb"})
    assert (down_fail.status_code, down_fail.json()) == (healthy_fail.status_code, healthy_fail.json())
    assert "X-Governance-Audit-Sequence" not in down_fail.headers
    ok = _compile("sem.ipynb")
    assert ok.status_code == 200 and "governance_audit_error" in ok.json() and "governance_audit" not in ok.json()


@pytest.fixture
def populated(env):
    for i in range(4):
        _notebook(env / "uploads", f"p{i}.ipynb")
        assert _compile(f"p{i}.ipynb").status_code == 200
    return env / "audit.db"


def _verify():
    return client.get("/governance/audit/verify").json()


@pytest.mark.parametrize("victim,expected", [("MAX", None), (1, 1), (2, 2)])
def test_deleted_records_are_detected_wherever_they_were(populated, victim, expected):
    assert _verify()["valid"] is True
    with sqlite3.connect(populated) as conn:
        target = conn.execute("SELECT MAX(sequence) FROM audit_records WHERE action = 'notebook.compile'").fetchone()[0] if victim == "MAX" else victim
        conn.execute("DELETE FROM audit_records WHERE sequence = ?", (target,))
    result = _verify()
    assert result["valid"] is False and result["first_broken_sequence"] is not None
    assert "missing" in result["reason"] or "previous_hash" in result["reason"] or "hash" in result["reason"]


def test_a_truncated_tail_is_detected_even_though_every_remaining_link_is_intact(populated):
    last = _verify()["checked"]  # verifying itself appended a record
    with sqlite3.connect(populated) as conn:
        conn.execute("DELETE FROM audit_records WHERE sequence > ?", (last - 1,))
    result = _verify()
    assert result["valid"] is False and "missing" in result["reason"]


def test_an_intended_purge_verifies_as_valid_and_tampering_after_it_is_still_caught(populated):
    service = audit.get_audit_service()
    assert service.purge() > 0
    assert _verify()["valid"] is True
    with sqlite3.connect(populated) as conn:
        conn.execute("UPDATE audit_records SET outcome = 'failure' WHERE sequence = (SELECT MAX(sequence) FROM audit_records)")
    assert _verify()["valid"] is False


def test_a_database_from_before_purges_were_tracked_is_upgraded_in_place(tmp_path):
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            "CREATE TABLE audit_records (sequence INTEGER PRIMARY KEY, action TEXT NOT NULL, actor TEXT NOT NULL, "
            "resource TEXT NOT NULL, outcome TEXT NOT NULL, occurred_at TEXT NOT NULL, metadata TEXT NOT NULL, "
            "previous_hash TEXT NOT NULL, record_hash TEXT NOT NULL);"
            "CREATE TABLE audit_meta (id INTEGER PRIMARY KEY CHECK (id = 1), last_sequence INTEGER NOT NULL, last_hash TEXT NOT NULL);"
        )
    service = audit.GovernanceAuditService(store=SqliteAuditStore(db))
    service.record(action="x", actor="a", resource="r", outcome="success")
    assert service.verify_chain().valid
    with sqlite3.connect(db) as conn:
        assert "purged_through" in {r[1] for r in conn.execute("PRAGMA table_info(audit_meta)")}


def test_the_application_documents_how_recovery_works(env):
    guide = client.get("/governance/audit/recovery").json()
    assert set(guide) == {"guarantees", "if_unavailable", "if_broken"}
    text = " ".join(sum(guide.values(), []))
    for phrase in ("Idempotency-Key", "restart", "governance_audit_error", "backup", "/governance/audit/health"):
        assert phrase in text
