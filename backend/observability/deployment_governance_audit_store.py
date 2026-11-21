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
import time
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
-- Filters (notebook history, action, outcome) page newest-first by sequence: these indexes let
-- SQLite walk only the matching rows instead of scanning the whole history. IF NOT EXISTS makes
-- this also the migration for databases created before the indexes existed.
CREATE INDEX IF NOT EXISTS idx_audit_resource_sequence ON audit_records (resource, sequence);
CREATE INDEX IF NOT EXISTS idx_audit_action_sequence ON audit_records (action, sequence);
CREATE INDEX IF NOT EXISTS idx_audit_outcome_sequence ON audit_records (outcome, sequence);
CREATE TABLE IF NOT EXISTS audit_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1), last_sequence INTEGER NOT NULL, last_hash TEXT NOT NULL,
    purged_through INTEGER NOT NULL DEFAULT 0
);
"""


class GovernanceAuditStorageError(RuntimeError):
    """The audit storage is misconfigured or unavailable (message is safe to show: no paths)."""

    configuration = False


class GovernanceAuditConfigurationError(GovernanceAuditStorageError):
    """The storage is configured wrongly (as opposed to failing while in use)."""

    configuration = True


class SqliteAuditStore:
    def __init__(self, path: str | Path, *, timeout_seconds: float = 30.0) -> None:
        self._path = Path(path)
        self._timeout = timeout_seconds
        self._local = threading.local()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        except (OSError, sqlite3.Error) as exc:
            raise GovernanceAuditConfigurationError(
                f"governance audit database could not be opened ({type(exc).__name__}); check that "
                "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH points to a writable location"
            ) from None

    def _initialize(self) -> None:
        """Create or upgrade the schema. Several processes may start against one new database at
        the same moment (workers, replicas): switching the journal mode and creating tables can then
        answer "locked" without waiting, so retry until the timeout instead of failing the start."""
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                conn = self._connect()
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA)
                conn.execute("INSERT OR IGNORE INTO audit_meta (id, last_sequence, last_hash) VALUES (1, 0, ?)", (GENESIS_HASH,))
                if "purged_through" not in {r[1] for r in conn.execute("PRAGMA table_info(audit_meta)")}:
                    # a database created before purges were tracked
                    try:
                        conn.execute("ALTER TABLE audit_meta ADD COLUMN purged_through INTEGER NOT NULL DEFAULT 0")
                    except sqlite3.OperationalError as exc:
                        if "duplicate column" not in str(exc):  # another process added it first
                            raise
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) and "busy" not in str(exc) or time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=self._timeout, isolation_level=None)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def append(self, build: Callable[[int, str], Any]) -> Any:
        """Run build(next_sequence, previous_hash) -> record, and store it atomically."""
        return self._append(build, None)[0]

    def append_once(self, build: Callable[[int, str], Any], action: str, key: str) -> tuple[Any, bool]:
        """Like append, but when a record with this action and idempotency key already exists,
        return (that stored row, False) and write nothing. Checked inside the same transaction, so
        concurrent retries cannot both insert."""
        return self._append(build, (action, key))

    def _append(self, build, dedupe):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if dedupe is not None:
                    found = conn.execute(
                        "SELECT * FROM audit_records WHERE action = ? AND json_extract(metadata, '$.idempotency_key') = ? LIMIT 1",
                        dedupe,
                    ).fetchone()
                    if found is not None:
                        conn.execute("COMMIT")
                        item = dict(found)
                        item["occurred_at"] = datetime.fromisoformat(item["occurred_at"])
                        item["metadata"] = json.loads(item["metadata"])
                        return item, False
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
        return record, True

    def head(self) -> dict:
        """The chain head the store remembers independently of the rows (for detecting lost records)."""
        try:
            return dict(self._connect().execute(
                "SELECT last_sequence, last_hash, purged_through FROM audit_meta WHERE id = 1").fetchone())
        except sqlite3.Error as exc:
            raise GovernanceAuditStorageError(f"governance audit read failed ({type(exc).__name__})") from None

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

    def iter_rows(self, batch_size: int = 1000):
        """Yield every row in ascending sequence order, one keyset batch at a time, so a caller can
        walk an arbitrarily long history with memory bounded by the batch size."""
        after = 0
        while True:
            try:
                batch = self._connect().execute(
                    "SELECT * FROM audit_records WHERE sequence > ? ORDER BY sequence LIMIT ?",
                    (after, batch_size),
                ).fetchall()
            except sqlite3.Error as exc:
                raise GovernanceAuditStorageError(f"governance audit read failed ({type(exc).__name__})") from None
            if not batch:
                return
            for row in batch:
                item = dict(row)
                item["occurred_at"] = datetime.fromisoformat(item["occurred_at"])
                item["metadata"] = json.loads(item["metadata"])
                yield item
            after = batch[-1]["sequence"]

    def search(self, query) -> list[dict]:
        """Newest-first rows matching an AuditQuery, filtered and limited inside SQLite."""
        clauses, params = [], []
        for column, value in (("action", query.action), ("actor", query.actor),
                              ("resource", query.resource), ("outcome", query.outcome)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if query.resources is not None:
            clauses.append("resource IN (" + ",".join("?" * len(query.resources)) + ")")
            params.extend(query.resources)
        if query.before_sequence is not None:
            clauses.append("sequence < ?")
            params.append(query.before_sequence)
        if query.action_prefix is not None:
            escaped = query.action_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("action LIKE ? ESCAPE '\\'")
            params.append(escaped + "%")
        sql = "SELECT * FROM audit_records"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY sequence DESC LIMIT ?"
        try:
            out = []
            for row in self._connect().execute(sql, (*params, query.limit)).fetchall():
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
            conn.execute("BEGIN IMMEDIATE")
            try:
                removed = conn.execute("DELETE FROM audit_records").rowcount
                conn.execute("UPDATE audit_meta SET purged_through = last_sequence WHERE id = 1")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            return removed
        except sqlite3.Error as exc:
            raise GovernanceAuditStorageError(f"governance audit purge failed ({type(exc).__name__})") from None

    def count(self) -> int:
        return self._connect().execute("SELECT COUNT(*) FROM audit_records").fetchone()[0]


def _in_container(environ) -> bool:
    import os

    return bool(environ.get("KUBERNETES_SERVICE_HOST")) or os.path.exists("/.dockerenv")


def audit_storage_status(environ=None) -> dict:
    """Describe (without paths or credentials) whether audit storage is configured and durable.

    "durable" is only True for sqlite whose directory is not on a container's ephemeral layer. In a
    container the data directory must be a mounted volume; set NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT=1
    to assert that it is (the generated compose and Kubernetes files do)."""
    import os
    import tempfile

    environ = os.environ if environ is None else environ
    backend = environ.get("NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "").strip().lower() or "memory"
    production = environ.get("NOTEBOOK2API_ENVIRONMENT", "").strip().lower() in {"production", "prod"}
    issues: list[str] = []
    if backend != "sqlite":
        issues.append("audit records are process-local and are lost on restart")
        return {"backend": backend, "durable": False, "production": production, "issues": issues}
    path = Path(environ.get("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", "data/notebook2api.db")).resolve()
    asserted = environ.get("NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT", "").strip().lower() in {"1", "true", "yes"}
    if not asserted:
        tmp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp"), Path("/var/tmp")}
        if any(root == path or root in path.parents for root in tmp_roots):
            issues.append("the audit database is in a temporary directory")
        elif _in_container(environ):
            try:
                probe = path.parent
                while not probe.exists() and probe != probe.parent:
                    probe = probe.parent
                if os.stat(probe).st_dev == os.stat("/").st_dev:
                    issues.append(
                        "the audit database is on the container's ephemeral filesystem; mount a volume "
                        "there and set NOTEBOOK2API_GOVERNANCE_VOLUME_PERSISTENT=1"
                    )
            except OSError:
                pass
    return {"backend": backend, "durable": not issues, "production": production, "issues": issues}


def validate_audit_storage_for_startup(environ=None) -> dict:
    """Fail fast on unusable production audit storage; otherwise return audit_storage_status()."""
    from .deployment_governance_audit import build_audit_service_from_env

    status = audit_storage_status(environ)
    # Refuse non-durable production storage before touching it: nothing should be created there.
    if status["production"] and status["backend"] == "sqlite" and not status["durable"]:
        raise GovernanceAuditConfigurationError(
            "production audit storage is not durable: " + "; ".join(status["issues"])
        )
    build_audit_service_from_env(environ)  # raises GovernanceAuditStorageError when misconfigured
    return status
