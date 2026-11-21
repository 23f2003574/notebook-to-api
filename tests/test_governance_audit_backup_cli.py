"""audit-db backup / restore / verify-file."""
import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

from backend.observability.deployment_governance_audit import GovernanceAuditService
from backend.observability.deployment_governance_audit_backup import AuditDbError, backup, restore, verify_file
from backend.observability.deployment_governance_audit_store import AuditKeyring, SqliteAuditStore
import pytest

ROOT = Path(__file__).resolve().parent.parent
KEY = "s" * 32


def _make(path, n=5, key=None, start=0):
    service = GovernanceAuditService(store=SqliteAuditStore(path, keyring=AuditKeyring(key) if key else None))
    for i in range(start, start + n):
        service.record(action="notebook.compile", actor="u", resource=f"notebook:n{i}", outcome="success", metadata={"i": i})
    return service


def _cli(*args, env=None):
    import os

    environment = {**os.environ, "PYTHONPATH": str(ROOT), **(env or {})}
    proc = subprocess.run([sys.executable, "-m", "backend.cli", "audit-db", *args], capture_output=True, text=True,
                          timeout=120, cwd=ROOT, env=environment)
    return proc.returncode, proc.stdout + proc.stderr


def test_a_backup_taken_during_concurrent_writes_verifies(tmp_path):
    db = tmp_path / "live.db"
    _make(db, 3)
    code = textwrap.dedent("""
        import sys
        from backend.observability.deployment_governance_audit import GovernanceAuditService
        from backend.observability.deployment_governance_audit_store import SqliteAuditStore
        s = GovernanceAuditService(store=SqliteAuditStore(sys.argv[1]))
        for i in range(400):
            s.record(action="notebook.compile", actor="w", resource="notebook:x", outcome="success", metadata={"i": i})
    """)
    writers = [subprocess.Popen([sys.executable, "-c", code, str(db)], cwd=ROOT) for _ in range(3)]
    results = [backup(db, tmp_path / f"b{i}.db") for i in range(4)]
    assert all(w.wait(120) == 0 for w in writers)
    assert all(r["valid"] and r["integrity"] == "unkeyed" for r in results)
    assert results[-1]["last_sequence"] >= results[0]["last_sequence"]


def test_restore_round_trip_preserves_sequence_head_and_indexes(tmp_path):
    db = tmp_path / "a.db"
    original = _make(db, 6)
    head = original.head()
    backup(db, tmp_path / "b.db")
    _make(db, 4, start=6)  # the live database moves on
    with pytest.raises(AuditDbError, match="newer records") as refused:
        restore(tmp_path / "b.db", db)
    assert refused.value.code == 2
    result = restore(tmp_path / "b.db", db, force=True)
    assert result["valid"] and result["last_sequence"] == head["last_sequence"] and result["last_hash"] == head["last_hash"]
    assert Path(result["previous_database"]).is_file()
    assert GovernanceAuditService(store=SqliteAuditStore(Path(result["previous_database"]))).size() == 10
    conn = sqlite3.connect(db)
    assert {"idx_audit_resource_sequence", "idx_audit_action_sequence"} <= {r[1] for r in conn.execute("PRAGMA index_list(audit_records)")}
    # the chain continues from the restored head
    again = GovernanceAuditService(store=SqliteAuditStore(db))
    assert again.record(action="a", actor="u", resource="r", outcome="success").sequence == head["last_sequence"] + 1


def test_restore_into_a_missing_or_older_database_needs_no_force(tmp_path):
    db = tmp_path / "a.db"
    _make(db, 3)
    backup(db, tmp_path / "old.db")
    _make(db, 3, start=3)
    backup(db, tmp_path / "new.db")
    fresh = tmp_path / "fresh.db"
    assert restore(tmp_path / "new.db", fresh)["previous_database"] is None
    older = tmp_path / "older.db"
    backup(tmp_path / "old.db", older)
    assert restore(tmp_path / "new.db", older)["last_sequence"] == 6


def test_a_diverged_database_is_not_replaced_without_force(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _make(a, 3)
    backup(a, tmp_path / "base.db")
    _make(a, 2, start=3)
    other = tmp_path / "other.db"
    _make(other, 5, start=100)  # a different history of the same length
    with pytest.raises(AuditDbError, match="diverged|newer"):
        restore(tmp_path / "base.db", other)
    assert b.exists() is False


def test_a_tampered_backup_is_rejected_and_nothing_changes(tmp_path):
    db = tmp_path / "a.db"
    _make(db, 5)
    backup(db, tmp_path / "b.db")
    conn = sqlite3.connect(tmp_path / "b.db")
    conn.execute("UPDATE audit_records SET actor = 'evil' WHERE sequence = 2")
    conn.commit()
    conn.close()
    before = db.read_bytes()
    with pytest.raises(AuditDbError, match="failed verification") as refused:
        restore(tmp_path / "b.db", db, force=True)
    assert refused.value.code == 3 and db.read_bytes() == before
    assert not verify_file(tmp_path / "b.db")["valid"]


def test_keyed_and_unkeyed_formats_verify(tmp_path):
    keyed = tmp_path / "k.db"
    _make(keyed, 4, key=KEY)
    env = {"NOTEBOOK2API_GOVERNANCE_AUDIT_HMAC_KEY": KEY}
    out = backup(keyed, tmp_path / "kb.db", environ=env)
    assert out["valid"] and out["integrity"] == "keyed"
    with pytest.raises(AuditDbError, match="HMAC_KEY"):
        verify_file(tmp_path / "kb.db", environ={})  # keyed backup, no key available
    assert restore(tmp_path / "kb.db", tmp_path / "kr.db", environ=env)["integrity"] == "keyed"


def test_verify_file_never_modifies_the_file_and_rejects_non_audit_files(tmp_path):
    db = tmp_path / "a.db"
    _make(db, 2)
    before = db.read_bytes()
    verify_file(db)
    assert db.read_bytes() == before
    junk = tmp_path / "junk.db"
    junk.write_text("not a database")
    with pytest.raises(AuditDbError):
        verify_file(junk)
    with pytest.raises(AuditDbError, match="does not exist"):
        backup(tmp_path / "missing.db", tmp_path / "x.db")


def test_the_cli_commands_and_exit_codes(tmp_path):
    db = tmp_path / "a.db"
    _make(db, 4)
    code, out = _cli("backup", "--database", str(db), "--output", str(tmp_path / "b.db"))
    assert code == 0 and "chain intact" in out
    code, out = _cli("backup", "--database", str(db), "--output", str(tmp_path / "b.db"))
    assert code == 2 and "already exists" in out
    code, out = _cli("verify-file", str(tmp_path / "b.db"), "--json")
    assert code == 0 and json.loads(out)["checked"] == 4
    _make(db, 2, start=4)
    code, out = _cli("restore", "--database", str(db), "--input", str(tmp_path / "b.db"))
    assert code == 2 and "newer records" in out
    code, out = _cli("restore", "--database", str(db), "--input", str(tmp_path / "b.db"), "--force")
    assert code == 0 and "previous database kept as" in out
    sqlite3.connect(tmp_path / "b.db").execute("PRAGMA user_version=1")
    bad = sqlite3.connect(tmp_path / "b.db")
    bad.execute("UPDATE audit_records SET outcome = 'failure' WHERE sequence = 1")
    bad.commit()
    code, out = _cli("verify-file", str(tmp_path / "b.db"))
    assert code == 3 and "BROKEN" in out
