"""A fixed-window request counter shared by every dashboard worker through SQLite.

The dashboard's default limiter keeps its windows in process memory, so N workers allow N times the
configured rate and a restart forgets every window. Set NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB to a
file on storage all workers share and each request is counted there instead: `hit` runs in one
BEGIN IMMEDIATE transaction, so concurrent workers never lose an increment, and the windows survive
a restart. Clients are stored as a SHA-256 digest (never the address or an API key); rows whose
window has ended are deleted on every hit, which bounds the table by the number of clients active
in the last window."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from contextlib import closing

_SCHEMA = ("CREATE TABLE IF NOT EXISTS rate_windows (client TEXT PRIMARY KEY, window_start REAL NOT NULL,"
           " count INTEGER NOT NULL)")


def database_path() -> str:
    return os.getenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB", "").strip()


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)  # we issue BEGIN IMMEDIATE ourselves
    if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":  # switching needs an exclusive lock
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS rate_windows_start ON rate_windows (window_start)")
    return conn


def client_id(client: str) -> str:
    return hashlib.sha256(client.encode("utf-8")).hexdigest()[:32]


def hit(path: str, client: str, now: float, window_seconds: float) -> tuple[float, int]:
    """Count one request (retrying briefly when another worker holds the database lock)."""
    delay = 0.02
    for attempt in range(8):
        try:
            return _hit(path, client, now, window_seconds)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 7:
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def _hit(path: str, client: str, now: float, window_seconds: float) -> tuple[float, int]:
    """Count one request from `client` and return (window_start, count) of its current window.
    Raises sqlite3.Error when the database cannot be used (the caller decides how to degrade)."""
    ident = client_id(client)
    with closing(_connect(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM rate_windows WHERE window_start <= ?", (now - window_seconds,))
            row = conn.execute("SELECT window_start, count FROM rate_windows WHERE client = ?", (ident,)).fetchone()
            window_start, count = (row[0], row[1] + 1) if row else (now, 1)
            conn.execute("INSERT OR REPLACE INTO rate_windows (client, window_start, count) VALUES (?,?,?)",
                         (ident, window_start, count))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return window_start, count
