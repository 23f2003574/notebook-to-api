"""Durable SQLite storage for the governance audit trail.

The hash chain stays owned by GovernanceAuditService; this module only makes appends durable and
safe across threads and processes. Every append runs in one BEGIN IMMEDIATE transaction that reads
the chain head and writes the next record, so concurrent writers are serialized by SQLite and can
neither reuse a sequence number nor fork the chain. A small meta row remembers the chain head so a
purge (which deletes rows) cannot restart the chain.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

GENESIS_HASH = "0" * 64
_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_records (
    sequence INTEGER PRIMARY KEY,
    action TEXT NOT NULL, actor TEXT NOT NULL, resource TEXT NOT NULL, outcome TEXT NOT NULL,
    occurred_at TEXT NOT NULL, metadata TEXT NOT NULL,
    previous_hash TEXT NOT NULL, record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1), last_sequence INTEGER NOT NULL, last_hash TEXT NOT NULL
);
"""


class GovernanceAuditStorageError(RuntimeError):
    """The audit storage is misconfigured or unavailable (message is safe to show: no paths)."""


class SqliteAuditStore:
    def __init__(self, path: str | Path, *, timeout_seconds: float = 30.0) -> None:
        self._path = Path(path)
        self._timeout = timeout_seconds
        self._local = threading.local()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                conn.execute("INSERT OR IGNORE INTO audit_meta VALUES (1, 0, ?)", (GENESIS_HASH,))
                conn.execute("PRAGMA journal_mode=WAL")
        except (OSError, sqlite3.Error) as exc:
            raise GovernanceAuditStorageError(
                f"governance audit database could not be opened ({type(exc).__name__}); check that "
                "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH points to a writable location"
            ) from None

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=self._timeout, isolation_level=None)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def append(self, build: Callable[[int, str], Any]) -> Any:
        """Run build(next_sequence, previous_hash) -> record, and store it atomically."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                head = conn.execute("SELECT last_sequence, last_hash FROM audit_meta WHERE id = 1").fetchone()
                record = build(head["last_sequence"] + 1, head["last_hash"])
                conn.execute(
                    "INSERT INTO audit_records VALUES (?,?,?,?,?,?,?,?,?)",
                    (record.sequence, record.action, record.actor, record.resource, record.outcome,
                     record.occurred_at.isoformat(), json.dumps(dict(record.metadata)),
                     record.previous_hash, record.record_hash),
                )
                conn.execute("UPDATE audit_meta SET last_sequence = ?, last_hash = ? WHERE id = 1",
                             (record.sequence, record.record_hash))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        except sqlite3.Error as exc:
            raise GovernanceAuditStorageError(f"governance audit write failed ({type(exc).__name__})") from None
        return record

    def rows(self, sequence: int | None = None) -> list[dict]:
        try:
            conn = self._connect()
            if sequence is None:
                cur = conn.execute("SELECT * FROM audit_records ORDER BY sequence")
            else:
                cur = conn.execute("SELECT * FROM audit_records WHERE sequence = ?", (sequence,))
            out = []
            for row in cur.fetchall():
                item = dict(row)
                item["occurred_at"] = datetime.fromisoformat(item["occurred_at"])
                item["metadata"] = json.loads(item["metadata"])
                out.append(item)
            return out
        except sqlite3.Error as exc:
            raise GovernanceAuditStorageError(f"governance audit read failed ({type(exc).__name__})") from None

    def clear(self) -> int:
        """Delete every row but keep the chain head, so a purge cannot restart the chain."""
        try:
            conn = self._connect()
            return conn.execute("DELETE FROM audit_records").rowcount
        except sqlite3.Error as exc:
            raise GovernanceAuditStorageError(f"governance audit purge failed ({type(exc).__name__})") from None

    def count(self) -> int:
        return self._connect().execute("SELECT COUNT(*) FROM audit_records").fetchone()[0]
