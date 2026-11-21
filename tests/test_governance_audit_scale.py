"""Verification and queries over long audit histories: bounded memory, read-only, indexed."""
import sqlite3
import threading
import time
import tracemalloc

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.observability import deployment_governance_api as api
from backend.observability.deployment_governance_audit import GovernanceAuditService, reset_audit_service
from backend.observability.deployment_governance_audit_store import SqliteAuditStore


@pytest.fixture
def durable(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(db))
    reset_audit_service()
    app = FastAPI()
    app.include_router(api.health_router)
    yield db, TestClient(app), api._build_audit_service()
    reset_audit_service()


def _fill(service, count):
    for i in range(count):
        service.record(action="notebook.compile" if i % 2 else "notebook.deploy", actor="u",
                       resource=f"notebook:n{i % 50}.ipynb", outcome="success" if i % 7 else "failure",
                       metadata={"i": i})


def test_get_verify_is_read_only_and_post_records_once_per_key(durable):
    db, client, service = durable
    _fill(service, 5)
    for _ in range(3):
        assert client.get("/governance/audit/verify").json() == {"integrity": "unkeyed",
            "valid": True, "checked": 5, "first_broken_sequence": None, "reason": None}
    assert service.size() == 5

    first = client.post("/governance/audit/verify", headers={"Idempotency-Key": "k1"}).json()
    again = client.post("/governance/audit/verify", headers={"Idempotency-Key": "k1"}).json()
    assert first["valid"] and again["valid"]
    assert service.size() == 6
    assert service.latest(1)[0].action == "audit_verification"


def test_limits_are_capped(durable):
    _, client, _ = durable
    assert client.get("/governance/audit/latest?limit=201").status_code == 422
    assert client.get("/governance/audit?limit=201").status_code == 422
    assert client.get("/governance/audit/latest?limit=200").status_code == 200


def test_filters_use_the_indexes_and_old_databases_are_migrated(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE audit_records (sequence INTEGER PRIMARY KEY, action TEXT NOT NULL, actor TEXT NOT NULL,
          resource TEXT NOT NULL, outcome TEXT NOT NULL, occurred_at TEXT NOT NULL, metadata TEXT NOT NULL,
          previous_hash TEXT NOT NULL, record_hash TEXT NOT NULL);
        CREATE TABLE audit_meta (id INTEGER PRIMARY KEY CHECK (id = 1), last_sequence INTEGER NOT NULL,
          last_hash TEXT NOT NULL, purged_through INTEGER NOT NULL DEFAULT 0);""")
    conn.close()
    SqliteAuditStore(db)  # opening an existing database adds the indexes
    conn = sqlite3.connect(db)
    names = {r[1] for r in conn.execute("PRAGMA index_list(audit_records)")}
    assert {"idx_audit_resource_sequence", "idx_audit_action_sequence", "idx_audit_outcome_sequence"} <= names
    for column in ("resource", "action", "outcome"):
        plan = " ".join(str(r) for r in conn.execute(
            f"EXPLAIN QUERY PLAN SELECT * FROM audit_records WHERE {column} = ? ORDER BY sequence DESC LIMIT 5", ("x",)))
        assert f"idx_audit_{column}_sequence" in plan, plan


def test_verification_streams_with_bounded_memory(tmp_path):
    service = GovernanceAuditService(store=SqliteAuditStore(tmp_path / "big.db"))
    _fill(service, 3000)
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    result = service.verify_chain(record=False)
    peak = tracemalloc.get_traced_memory()[1] - baseline
    tracemalloc.stop()
    assert result.valid and result.checked == 3000
    # Batches of 1000 rows: far below holding 3000 records at once twice, as it used to.
    assert peak < 4 * 1024 * 1024, peak


def test_tamper_deletion_and_purge_are_still_detected_by_the_stream(tmp_path):
    db = tmp_path / "t.db"
    service = GovernanceAuditService(store=SqliteAuditStore(db))
    _fill(service, 2500)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE audit_records SET actor = 'x' WHERE sequence = 1700")
    conn.commit()
    broken = service.verify_chain(record=False)
    assert not broken.valid and broken.first_broken_sequence == 1700
    conn.execute("UPDATE audit_records SET actor = 'u' WHERE sequence = 1700")
    conn.execute("DELETE FROM audit_records WHERE sequence = 2500")
    conn.commit()
    assert "missing" in service.verify_chain(record=False).reason
    conn.close()
    other = GovernanceAuditService(store=SqliteAuditStore(tmp_path / "p.db"))
    _fill(other, 10)
    other.purge()
    assert other.verify_chain(record=False).valid


def test_health_stays_responsive_while_a_long_verification_runs(durable):
    db, client, service = durable
    _fill(service, 4000)
    done = threading.Event()
    threading.Thread(target=lambda: (client.get("/governance/audit/verify"), done.set())).start()
    answers = 0
    while not done.is_set() and answers < 200:
        assert client.get("/governance/audit/health").status_code == 200
        answers += 1
        time.sleep(0.001)
    assert done.wait(60)
