"""Keyed (HMAC) integrity: a rewritten chain is detected when a key is configured."""
import json
import sqlite3
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.observability import deployment_governance_api as api
from backend.observability.deployment_governance_audit import (
    GENESIS_HASH, GovernanceAuditService, _compute_record_hash, build_audit_service_from_env, reset_audit_service)
from backend.observability.deployment_governance_audit_store import (
    AuditKeyring, GovernanceAuditConfigurationError, SqliteAuditStore)

KEY = "k" * 32
ROOT = Path(__file__).resolve().parent.parent


def _service(db, key=KEY, epoch="1", previous=None, allow_loss=False):
    keyring = AuditKeyring(key, epoch, previous) if key else None
    return GovernanceAuditService(store=SqliteAuditStore(db, keyring=keyring, allow_key_loss=allow_loss))


def _fill(service, n=6):
    for i in range(n):
        service.record(action="notebook.compile", actor="u", resource=f"notebook:n{i}.ipynb", outcome="success", metadata={"i": i})


def _rewrite_history(db, sequence, new_actor):
    """What an attacker with write access does: change a record, then recompute every SHA-256 hash
    after it and the stored head, so the plain chain is perfectly consistent."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    previous = GENESIS_HASH
    for row in conn.execute("SELECT * FROM audit_records ORDER BY sequence").fetchall():
        actor = new_actor if row["sequence"] == sequence else row["actor"]
        if row["sequence"] < sequence:
            previous = row["record_hash"]
            continue
        digest = _compute_record_hash(
            sequence=row["sequence"], action=row["action"], actor=actor, resource=row["resource"],
            outcome=row["outcome"], occurred_at=datetime.fromisoformat(row["occurred_at"]),
            metadata=json.loads(row["metadata"]), previous_hash=previous)
        conn.execute("UPDATE audit_records SET actor=?, previous_hash=?, record_hash=? WHERE sequence=?",
                     (actor, previous, digest, row["sequence"]))
        conn.execute("UPDATE audit_meta SET last_hash=? WHERE last_sequence=?", (digest, row["sequence"]))
        previous = digest
    conn.commit()
    conn.close()


def test_a_keyed_chain_verifies_and_reports_keyed(tmp_path):
    service = _service(tmp_path / "a.db")
    _fill(service)
    result = service.verify_chain(record=False)
    assert result.valid and result.integrity == "keyed" and result.checked == 6


def test_a_full_sha256_rewrite_is_detected_with_a_key_and_invisible_without_one(tmp_path):
    db = tmp_path / "a.db"
    _fill(_service(db))
    _rewrite_history(db, 3, "attacker")
    keyed = _service(db).verify_chain(record=False)
    assert not keyed.valid and keyed.integrity == "keyed" and keyed.first_broken_sequence == 3
    assert "keyed integrity" in keyed.reason
    # the same rewrite passes an unkeyed (SHA-256 only) reading: that is the documented limit
    plain = _service(tmp_path / "b.db", key=None)
    _fill(plain)
    _rewrite_history(tmp_path / "b.db", 3, "attacker")
    verdict = plain.verify_chain(record=False)
    assert verdict.valid and verdict.integrity == "unkeyed"


def test_rewriting_the_head_and_purge_point_is_detected(tmp_path):
    db = tmp_path / "a.db"
    service = _service(db)
    _fill(service)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM audit_records WHERE sequence = 6")
    conn.execute("UPDATE audit_meta SET last_sequence = 5, last_hash = (SELECT record_hash FROM audit_records WHERE sequence = 5)")
    conn.commit()
    conn.close()
    result = service.verify_chain(record=False)
    assert not result.valid and "head" in result.reason
    # a purge through the store is legitimate and re-tags the head
    other = _service(tmp_path / "p.db")
    _fill(other)
    other.purge()
    assert other.verify_chain(record=False).valid


def test_an_existing_unkeyed_database_stays_readable_and_the_old_part_is_bound_by_the_first_keyed_record(tmp_path):
    db = tmp_path / "a.db"
    old = _service(db, key=None)
    _fill(old, 4)
    assert old.verify_chain(record=False).integrity == "unkeyed"
    keyed = _service(db)
    assert keyed.verify_chain(record=False).integrity == "unkeyed"  # nothing keyed has been written yet
    _fill(keyed, 3)
    result = keyed.verify_chain(record=False)
    assert result.valid and result.integrity == "keyed" and result.checked == 7
    _rewrite_history(db, 2, "attacker")  # inside the previously unkeyed part
    assert not keyed.verify_chain(record=False).valid


def test_a_keyed_database_needs_its_key_and_key_loss_is_explicit(tmp_path):
    db = tmp_path / "a.db"
    _fill(_service(db))
    with pytest.raises(GovernanceAuditConfigurationError, match="HMAC_KEY") as info:
        _service(db, key=None)
    assert KEY not in str(info.value)
    lost = _service(db, key=None, allow_loss=True)
    result = lost.verify_chain(record=False)
    assert result.valid and result.integrity == "keyed_unverified"
    wrong = _service(db, key="w" * 32).verify_chain(record=False)
    assert not wrong.valid


def test_key_rotation_keeps_old_records_verifiable(tmp_path):
    db = tmp_path / "a.db"
    _fill(_service(db), 3)
    rotated = _service(db, key="n" * 32, epoch="2", previous={"1": KEY})
    _fill(rotated, 2)
    assert rotated.verify_chain(record=False).integrity == "keyed" and rotated.verify_chain(record=False).valid
    without_old = _service(db, key="n" * 32, epoch="2")
    assert without_old.verify_chain(record=False).integrity == "keyed_unverified"


def test_concurrent_writers_keep_a_keyed_chain_valid(tmp_path):
    db = tmp_path / "c.db"
    _service(db)
    code = textwrap.dedent("""
        import sys
        from backend.observability.deployment_governance_audit import GovernanceAuditService
        from backend.observability.deployment_governance_audit_store import AuditKeyring, SqliteAuditStore
        s = GovernanceAuditService(store=SqliteAuditStore(sys.argv[1], keyring=AuditKeyring(sys.argv[2])))
        for i in range(25):
            s.record(action="notebook.compile", actor="w", resource="notebook:x", outcome="success", metadata={"i": i})
    """)
    procs = [subprocess.Popen([sys.executable, "-c", code, str(db), KEY], cwd=ROOT) for _ in range(4)]
    assert all(p.wait(120) == 0 for p in procs)
    result = _service(db).verify_chain(record=False)
    assert result.valid and result.checked == 100 and result.integrity == "keyed"


def test_the_environment_enables_keyed_integrity_and_the_key_is_never_exposed(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(tmp_path / "e.db"))
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_AUDIT_HMAC_KEY", KEY)
    reset_audit_service()
    app = FastAPI()
    app.include_router(api.health_router)
    client = TestClient(app)
    api._build_audit_service().record(action="notebook.compile", actor="u", resource="notebook:a", outcome="success")
    head = client.get("/governance/audit/head").json()
    assert head["integrity"] == "keyed" and head["last_sequence"] == 1 and head["head_mac"].startswith("v1:1:")
    verify = client.get("/governance/audit/verify").json()
    assert verify["valid"] and verify["integrity"] == "keyed"
    health = client.get("/governance/audit/health").json()
    assert health["keyed_integrity"] is True
    everything = json.dumps([head, verify, health]) + client.get("/governance/audit/recovery").text
    assert KEY not in everything
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_AUDIT_HMAC_KEY", "short")
    with pytest.raises(GovernanceAuditConfigurationError):
        build_audit_service_from_env()
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_AUDIT_HMAC_KEY", KEY)
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "memory")
    with pytest.raises(GovernanceAuditConfigurationError, match="sqlite"):
        build_audit_service_from_env()
    reset_audit_service()
