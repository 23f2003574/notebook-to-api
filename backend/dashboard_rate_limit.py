"""A fixed-window request counter shared by every dashboard worker through SQLite.

The dashboard's default limiter keeps its windows in process memory, so N workers allow N times the
configured rate and a restart forgets every window. Set NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB to a
file on storage all workers share and each request is counted there instead: `hit` runs in one
BEGIN IMMEDIATE transaction, so concurrent workers never lose an increment, and the windows survive
a restart. Each thread keeps one connection per database (tables are created once, expired rows are swept
at most once a minute). With NOTEBOOK_API_DASHBOARD_RATE_LIMIT_FAIL_CLOSED=1 an unusable store makes the
dashboard answer 503 instead of limiting per worker. Clients are stored as a SHA-256 digest (never the address or an API key); rows whose
window has ended are deleted on every hit, which bounds the table by the number of clients active
in the last window."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time

TABLES = ("rate_windows", "auth_failures")  # request counts and failed-authentication counts, each with its own window
_SCHEMAS = tuple(f"CREATE TABLE IF NOT EXISTS {t} (client TEXT PRIMARY KEY, window_start REAL NOT NULL,"
                 f" count INTEGER NOT NULL)" for t in TABLES)


def database_path() -> str:
    return os.getenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_DB", "").strip()


_LOCAL = threading.local()  # one connection per thread and database path: sqlite3 connections are not thread-safe
CLEANUP_INTERVAL_SECONDS = 60.0
_LAST_CLEANUP: dict = {}
STATS = {"connections_opened": 0, "cleanups": 0}


def fail_closed() -> bool:
    return os.getenv("NOTEBOOK_API_DASHBOARD_RATE_LIMIT_FAIL_CLOSED", "").strip().lower() in {"1", "true", "yes"}


def _connect(path: str) -> sqlite3.Connection:
    """This thread's connection to `path` (opened, switched to WAL and given its tables once)."""
    if getattr(_LOCAL, "pid", None) != os.getpid():  # a forked worker must not reuse its parent's connections
        _LOCAL.pid, _LOCAL.conns = os.getpid(), {}
    conn = _LOCAL.conns.get(path)
    if conn is None:
        conn = sqlite3.connect(path, timeout=5, isolation_level=None)  # we issue BEGIN IMMEDIATE ourselves
        try:
            if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":  # switching needs an exclusive lock
                conn.execute("PRAGMA journal_mode=WAL")
            for schema, table in zip(_SCHEMAS, TABLES):
                conn.execute(schema)
                conn.execute(f"CREATE INDEX IF NOT EXISTS {table}_start ON {table} (window_start)")
        except BaseException:
            conn.close()
            raise
        _LOCAL.conns[path] = conn
        STATS["connections_opened"] += 1
    return conn


def _discard(path: str) -> None:
    conn = getattr(_LOCAL, "conns", {}).pop(path, None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def client_id(client: str) -> str:
    return hashlib.sha256(client.encode("utf-8")).hexdigest()[:32]


def hit(path: str, client: str, now: float, window_seconds: float, table: str = "rate_windows") -> tuple[float, int]:
    """Count one request (retrying briefly when another worker holds the database lock)."""
    delay = 0.02
    for attempt in range(8):
        try:
            return _hit(path, client, now, window_seconds, table)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 7:
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def _hit(path: str, client: str, now: float, window_seconds: float, table: str = "rate_windows") -> tuple[float, int]:
    """Count one request from `client` and return (window_start, count) of its current window.
    Expired rows are swept at most once per CLEANUP_INTERVAL_SECONDS per process, so a request costs one
    transaction. Raises sqlite3.Error when the database cannot be used (the caller decides how to degrade)."""
    assert table in TABLES
    ident = client_id(client)
    try:
        conn = _connect(path)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if now - _LAST_CLEANUP.get((path, table), 0.0) >= CLEANUP_INTERVAL_SECONDS:
                conn.execute(f"DELETE FROM {table} WHERE window_start <= ?", (now - window_seconds,))
                _LAST_CLEANUP[(path, table)] = now
                STATS["cleanups"] += 1
            row = conn.execute(f"SELECT window_start, count FROM {table} WHERE client = ?", (ident,)).fetchone()
            window_start, count = (row[0], row[1] + 1) if row and now - row[0] < window_seconds else (now, 1)
            conn.execute(f"INSERT OR REPLACE INTO {table} (client, window_start, count) VALUES (?,?,?)",
                         (ident, window_start, count))
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                _discard(path)
            raise
    except sqlite3.Error:
        _discard(path)
        raise
    return window_start, count




def peek(path: str, client: str, now: float, window_seconds: float, table: str = "auth_failures") -> int:
    """The count of `client`'s current window in `table` without counting a request (0 when it has ended)."""
    assert table in TABLES
    row = _connect(path).execute(
        f"SELECT window_start, count FROM {table} WHERE client = ?", (client_id(client),)).fetchone()
    return row[1] if row and now - row[0] < window_seconds else 0
