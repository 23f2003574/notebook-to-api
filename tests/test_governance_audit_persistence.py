"""The governance audit chain is durable, concurrency-safe, and never silently process-local in production."""
import sqlite3
import subprocess
import sys
import threading

import pytest

from backend.observability import deployment_governance_audit as audit
from backend.observability.deployment_governance_audit_store import (
    GovernanceAuditStorageError,
    SqliteAuditStore,
)


def _service(path):
    return audit.GovernanceAuditService(store=SqliteAuditStore(path))


def _rec(svc, n=1, **kw):
    return [svc.record(action="notebook.compile", actor="dashboard", resource=f"notebook:n{i}", outcome="success", metadata=kw) for i in range(n)]


def test_records_and_the_chain_survive_a_restart(tmp_path):
    db = tmp_path / "audit.db"
    first = _service(db)
    written = _rec(first, 3, version="v1")
    restarted = _service(db)
    assert [r.sequence for r in restarted.query()] == [3, 2, 1]
    assert restarted.get(2).record_hash == written[1].record_hash
    nxt = _rec(restarted)[0]
    assert nxt.sequence == 4 and nxt.previous_hash == written[2].record_hash
    assert restarted.verify_chain().valid


def test_concurrent_threads_never_reuse_a_sequence_or_fork_the_chain(tmp_path):
    db = tmp_path / "audit.db"
    svc = _service(db)
    errors = []

    def work():
        try:
            _rec(svc, 10)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert sorted(r.sequence for r in svc.query(audit.AuditQuery(limit=1000))) == list(range(1, 81))
    assert svc.verify_chain().valid


def test_concurrent_processes_share_one_chain(tmp_path):
    db = tmp_path / "audit.db"
    _service(db)
    code = (
        "import sys\n"
        "from backend.observability import deployment_governance_audit as a\n"
        "from backend.observability.deployment_governance_audit_store import SqliteAuditStore\n"
        "s = a.GovernanceAuditService(store=SqliteAuditStore(sys.argv[1]))\n"
        "[s.record(action='x', actor='p', resource='r', outcome='success') for _ in range(15)]\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", code, str(db)]) for _ in range(4)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    svc = _service(db)
    assert svc.size() == 60 and svc.verify_chain().valid


def test_tampering_with_the_database_is_detected_after_restart(tmp_path):
    db = tmp_path / "audit.db"
    _rec(_service(db), 3)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE audit_records SET outcome = 'failure' WHERE sequence = 2")
    result = _service(db).verify_chain()
    assert not result.valid and result.first_broken_sequence == 2


def test_purge_does_not_restart_the_chain_even_after_a_restart(tmp_path):
    db = tmp_path / "audit.db"
    first = _service(db)
    _rec(first, 2)
    assert first.purge() == 2
    after = _service(db)
    rec = _rec(after)[0]
    assert rec.sequence == 3 and rec.previous_hash != audit.GENESIS_HASH


def test_a_failed_write_leaves_no_partial_record(tmp_path):
    svc = _service(tmp_path / "audit.db")
    _rec(svc)
    with pytest.raises(Exception):
        svc.record(action="x", actor="a", resource="r", outcome="success", metadata={"bad": object()})
    assert svc.size() == 1 and _rec(svc)[0].sequence == 2 and svc.verify_chain().valid


def test_production_requires_an_explicit_backend():
    with pytest.raises(GovernanceAuditStorageError, match="PERSISTENCE_BACKEND"):
        audit.build_audit_service_from_env({"NOTEBOOK2API_ENVIRONMENT": "production"})


def test_production_accepts_sqlite_and_an_explicit_memory_choice(tmp_path):
    svc = audit.build_audit_service_from_env({
        "NOTEBOOK2API_ENVIRONMENT": "production",
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND": "sqlite",
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH": str(tmp_path / "a.db"),
    })
    _rec(svc)
    assert svc.size() == 1
    memory = audit.build_audit_service_from_env({
        "NOTEBOOK2API_ENVIRONMENT": "production", "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND": "memory"})
    assert memory._store is None


def test_development_default_stays_memory_and_bad_values_are_rejected():
    assert audit.build_audit_service_from_env({})._store is None
    with pytest.raises(GovernanceAuditStorageError, match="unsupported"):
        audit.build_audit_service_from_env({"NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND": "postgres"})


def test_an_unwritable_location_gives_an_actionable_error_without_the_path(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(GovernanceAuditStorageError) as exc:
        SqliteAuditStore(blocker / "sub" / "audit.db")
    assert "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH" in str(exc.value) and str(tmp_path) not in str(exc.value)


def test_the_dashboard_endpoints_read_the_durable_chain(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.dashboard import app

    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(tmp_path / "d.db"))
    audit.reset_audit_service()
    try:
        client = TestClient(app)
        _rec(audit.get_audit_service(), 2)
        assert len(client.get("/governance/audit").json()) == 2
        audit.reset_audit_service()  # a "restart"
        assert len(client.get("/governance/audit").json()) == 2
        assert client.get("/governance/audit/verify").json()["valid"] is True
    finally:
        audit.reset_audit_service()
