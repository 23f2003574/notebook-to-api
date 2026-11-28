"""Notebook children must not reach the dashboard key store, rate-limit database, key file or slot dir."""
import os
import sqlite3
import sys

import pytest

from backend.runtime import isolated_execution as iso
from tests.test_isolated_storage_hiding import needs_landlock


@pytest.fixture
def service(tmp_path, monkeypatch):
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "notebook2api.db").write_text("audit")
    keys = tmp_path / "keys"  # deliberately not beside the audit database
    keys.mkdir()
    from backend import dashboard_keys
    monkeypatch.setenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", str(audit / "notebook2api.db"))
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS_DB", str(keys / "keys.db"))
    monkeypatch.setenv("NOTEBOOK_API_ISOLATE_REQUIRE_HIDDEN", "1")
    dashboard_keys._connect(create=True).close()
    (keys / "keys.db-wal").write_text("wal")
    rate = tmp_path / "rate"
    rate.mkdir()
    (rate / "rate.db").write_text("rate")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB", str(rate / "rate.db"))
    keyfile = tmp_path / "keyfile"
    keyfile.write_text("name:secret:admin")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_KEYS_FILE", str(keyfile))
    slots = tmp_path / "slots"
    slots.mkdir()
    (slots / "slot-0.lock").write_text("")
    monkeypatch.setenv("NOTEBOOK_API_DASHBOARD_COMPILE_SLOT_DIR", str(slots))
    (tmp_path / "input.txt").write_text("notebook-input")
    return tmp_path


def test_protected_paths_list_every_service_file(service):
    found = iso.protected_paths()
    for expected in ("keys/keys.db", "keys/keys.db-wal", "rate/rate.db", "keyfile", "slots"):
        assert os.path.realpath(service / expected) in found, expected


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Landlock is Linux-only")
@needs_landlock
def test_child_cannot_read_or_forge_service_files(service):
    base = str(service)

    def child():
        out = {}
        for rel in ("keys/keys.db", "keys/keys.db-wal", "rate/rate.db", "keyfile", "slots/slot-0.lock"):
            for mode in ("rb", "ab"):
                try:
                    open(os.path.join(base, rel), mode).close()
                    out[f"{rel}:{mode}"] = "ALLOWED"
                except OSError:
                    pass
        try:
            conn = sqlite3.connect(os.path.join(base, "keys/keys.db"))
            conn.execute("INSERT INTO dashboard_keys (name,digest,scope,created_at) VALUES ('evil','00','admin','x')")
            conn.commit()
            out["forged_admin_key"] = "ALLOWED"
        except Exception:  # noqa: BLE001
            pass
        out["input"] = open(os.path.join(base, "input.txt")).read()
        return out

    assert iso.run_isolated(child, timeout=30) == {"input": "notebook-input"}
    from backend import dashboard_keys
    assert dashboard_keys.list_keys() == []
