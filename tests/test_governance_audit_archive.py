"""Archive + prune: verifiable archive, checkpointed chain, keyed mode, interruption and tampering."""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.observability import deployment_governance_audit_archive as arch
from backend.observability.deployment_governance_audit import GovernanceAuditService, build_audit_service_from_env
from backend.observability.deployment_governance_audit_backup import AuditDbError, restore, backup
from backend.observability.deployment_governance_audit_store import AuditKeyring, SqliteAuditStore

KEY = "k" * 32
ENV_KEYED = {"NOTEBOOK2API_GOVERNANCE_AUDIT_HMAC_KEY": KEY}


def _make(tmp_path, n=10, keyed=False, name="audit.db"):
    db = tmp_path / name
    store = SqliteAuditStore(db, keyring=AuditKeyring(KEY) if keyed else None)
    service = GovernanceAuditService(store=store)
    for i in range(n):
        service.record(action="notebook.compile", actor="u", resource=f"notebook:n{i}.ipynb", outcome="success", metadata={"i": i})
    return db


def _verify(db, keyed=False):
    store = SqliteAuditStore(db, keyring=AuditKeyring(KEY) if keyed else None)
    return GovernanceAuditService(store=store).verify_chain(record=False)


def _rows(db):
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT sequence FROM audit_records ORDER BY sequence")]
    finally:
        conn.close()


@pytest.mark.parametrize("keyed", [False, True])
def test_archive_then_prune_verifies_on_both_sides(tmp_path, keyed):
    db = _make(tmp_path, 10, keyed)
    env = ENV_KEYED if keyed else {}
    out = tmp_path / "a.jsonl"
    result = arch.archive_and_prune(db, out, through=6, environ=env)
    assert result["pruned"] == 6 and result["archived"] == 6
    assert _rows(db) == [7, 8, 9, 10, 11]  # 11 = the audit_archive record
    assert _verify(db, keyed).valid
    checked = arch.verify_archive(out, env)
    assert checked["valid"] and checked["count"] == 6 and checked["integrity"] == ("keyed" if keyed else "unkeyed")
    assert checked["last_hash"] == result["checkpoint_hash"]


def test_second_archive_continues_from_the_checkpoint(tmp_path):
    db = _make(tmp_path, 12)
    arch.archive_and_prune(db, tmp_path / "a1.jsonl", through=4, environ={})
    result = arch.archive_and_prune(db, tmp_path / "a2.jsonl", keep_latest=3, environ={})
    assert result["from_sequence"] == 5 and _verify(db).valid
    assert arch.verify_archive(tmp_path / "a2.jsonl", {})["previous_hash"] == arch.verify_archive(tmp_path / "a1.jsonl", {})["last_hash"]


def test_record_count_drops_only_after_a_successful_archive(tmp_path, monkeypatch):
    db = _make(tmp_path, 8)
    monkeypatch.setattr(arch, "verify_archive", lambda *a, **k: {"valid": False, "reason": "forced", "count": 0})
    with pytest.raises(AuditDbError, match="nothing was pruned"):
        arch.archive_and_prune(db, tmp_path / "a.jsonl", through=5, environ={})
    assert _rows(db) == list(range(1, 9)) and _verify(db).valid


def test_interrupted_write_prunes_nothing_and_a_retry_succeeds(tmp_path, monkeypatch):
    db = _make(tmp_path, 8)
    real = arch.os.replace
    monkeypatch.setattr(arch.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(AuditDbError, match="nothing was pruned"):
        arch.archive_and_prune(db, tmp_path / "a.jsonl", through=5, environ={})
    assert _rows(db) == list(range(1, 9)) and not (tmp_path / "a.jsonl").exists() and not (tmp_path / "a.jsonl.partial").exists()
    monkeypatch.setattr(arch.os, "replace", real)
    assert arch.archive_and_prune(db, tmp_path / "a.jsonl", through=5, environ={})["pruned"] == 5


def test_existing_archive_is_not_overwritten_without_force(tmp_path):
    db = _make(tmp_path, 6)
    out = tmp_path / "a.jsonl"
    out.write_text("keep me")
    with pytest.raises(AuditDbError, match="already exists"):
        arch.archive_and_prune(db, out, through=3, environ={})
    assert out.read_text() == "keep me" and _rows(db) == list(range(1, 7))


@pytest.mark.parametrize("keyed", [False, True])
def test_tampered_archive_is_rejected(tmp_path, keyed):
    db = _make(tmp_path, 8, keyed)
    env = ENV_KEYED if keyed else {}
    out = tmp_path / "a.jsonl"
    arch.archive_and_prune(db, out, through=5, environ=env)
    lines = out.read_text().split("\n")
    rec = json.loads(lines[2])
    rec["actor"] = "mallory"
    lines[2] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    out.write_text("\n".join(lines))
    assert not arch.verify_archive(out, env)["valid"]
    truncated = tmp_path / "t.jsonl"
    truncated.write_text("\n".join(out.read_text().split("\n")[:3]) + "\n")
    assert not arch.verify_archive(truncated, env)["valid"]
    garbage = tmp_path / "g.jsonl"
    garbage.write_text("nope\n")
    assert not arch.verify_archive(garbage, env)["valid"]


def test_keyed_archive_forged_with_recomputed_digest_fails_without_the_key(tmp_path):
    db = _make(tmp_path, 8, keyed=True)
    out = tmp_path / "a.jsonl"
    arch.archive_and_prune(db, out, through=5, environ=ENV_KEYED)
    other = {"NOTEBOOK2API_GOVERNANCE_AUDIT_HMAC_KEY": "z" * 32}
    assert arch.verify_archive(out, other)["valid"] is False
    assert arch.verify_archive(out, {})["integrity"] == "keyed_unverified"


@pytest.mark.parametrize("keyed", [False, True])
def test_rewriting_the_boundary_record_is_detected(tmp_path, keyed):
    db = _make(tmp_path, 10, keyed)
    arch.archive_and_prune(db, tmp_path / "a.jsonl", through=5, environ=ENV_KEYED if keyed else {})
    conn = sqlite3.connect(db)
    conn.execute("UPDATE audit_records SET previous_hash = ? WHERE sequence = 6", ("f" * 64,))
    conn.commit()
    conn.close()
    result = _verify(db, keyed)
    assert not result.valid and result.first_broken_sequence == 6


def test_blanking_the_checkpoint_of_a_keyed_chain_is_detected(tmp_path):
    db = _make(tmp_path, 10, keyed=True)
    arch.archive_and_prune(db, tmp_path / "a.jsonl", through=5, environ=ENV_KEYED)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE audit_meta SET purged_mac = '' WHERE id = 1")
    conn.commit()
    conn.close()
    assert not _verify(db, True).valid


def test_nothing_to_archive_and_dry_run_change_nothing(tmp_path):
    db = _make(tmp_path, 5)
    assert arch.archive_and_prune(db, tmp_path / "a.jsonl", keep_latest=10, environ={})["pruned"] == 0
    dry = arch.archive_and_prune(db, tmp_path / "a.jsonl", through=3, dry_run=True, environ={})
    assert dry["dry_run"] and dry["archived"] == 3 and not (tmp_path / "a.jsonl").exists() and _rows(db) == [1, 2, 3, 4, 5]
    with pytest.raises(AuditDbError):
        arch.archive_and_prune(db, tmp_path / "a.jsonl", environ={})  # no selector


def test_newest_record_is_never_pruned(tmp_path):
    db = _make(tmp_path, 5)
    arch.archive_and_prune(db, tmp_path / "a.jsonl", through=99, environ={})
    assert 5 in _rows(db) and _verify(db).valid


def test_older_than_days_selects_by_age(tmp_path):
    db = _make(tmp_path, 6)
    now = datetime.now(timezone.utc)
    assert arch.archive_and_prune(db, tmp_path / "a.jsonl", older_than_days=1, environ={}, now=now)["pruned"] == 0
    later = now + timedelta(days=3)  # every record is now more than a day old; the newest still stays
    result = arch.archive_and_prune(db, tmp_path / "a.jsonl", older_than_days=1, environ={}, now=later)
    assert result["pruned"] == 5 and _verify(db).valid


def test_broken_chain_is_refused(tmp_path):
    db = _make(tmp_path, 6)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE audit_records SET actor = 'x' WHERE sequence = 2")
    conn.commit()
    conn.close()
    with pytest.raises(AuditDbError, match="refusing"):
        arch.archive_and_prune(db, tmp_path / "a.jsonl", through=3, environ={})
    assert _rows(db) == list(range(1, 7))


def test_backup_and_restore_work_on_a_pruned_database(tmp_path):
    db = _make(tmp_path, 8)
    arch.archive_and_prune(db, tmp_path / "a.jsonl", through=4, environ={})
    backup(db, tmp_path / "b.db", environ={})
    target = tmp_path / "restored.db"
    restore(tmp_path / "b.db", target, environ={})
    assert _verify(target).valid and _rows(target)[0] == 5


def test_service_built_from_env_keeps_recording_after_a_prune(tmp_path, monkeypatch):
    db = _make(tmp_path, 6)
    arch.archive_and_prune(db, tmp_path / "a.jsonl", through=3, environ={})
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(db))
    service = build_audit_service_from_env()
    service.record(action="notebook.compile", actor="u", resource="notebook:z.ipynb", outcome="success", metadata={})
    assert service.verify_chain(record=False).valid


def test_cli_archive_and_verify(tmp_path):
    import subprocess

    db = _make(tmp_path, 9)
    root = Path(__file__).resolve().parent.parent
    out = tmp_path / "cli.jsonl"
    run = lambda *a: subprocess.run([sys.executable, "-m", "backend.cli", "audit-db", *a], cwd=root, capture_output=True, text=True)  # noqa: E731
    dry = run("archive", "--database", str(db), "--output", str(out), "--keep-latest", "2", "--dry-run")
    assert dry.returncode == 0 and "would archive" in dry.stdout and _rows(db) == list(range(1, 10))
    done = run("archive", "--database", str(db), "--output", str(out), "--keep-latest", "2", "--json")
    assert done.returncode == 0 and json.loads(done.stdout)["pruned"] == 7
    assert run("verify-archive", str(out)).returncode == 0
    out.write_text(out.read_text().replace('"actor":"u"', '"actor":"v"', 1))
    bad = run("verify-archive", str(out))
    assert bad.returncode == 3 and "INVALID" in bad.stdout
    assert run("archive", "--database", str(db), "--output", str(out)).returncode == 1  # no selector
