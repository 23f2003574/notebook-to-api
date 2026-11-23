"""Read-only size and age statistics of the governance audit database, with warning thresholds.

The audit chain only grows until `audit-db archive` prunes it. These numbers let an operator see
the growth before it becomes a problem. Nothing here writes to the database or appends a record:
the file is opened read-only and only counts, dates and byte sizes are read -- never the history.

Thresholds (0, empty or invalid disables one):
  NOTEBOOK2API_GOVERNANCE_AUDIT_WARN_RECORDS  warn at or above this many records
  NOTEBOOK2API_GOVERNANCE_AUDIT_WARN_BYTES    warn at or above this many bytes on disk
Crossing one is a warning only: a working audit store is never reported unhealthy because of it.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

RECORDS_ENV = "NOTEBOOK2API_GOVERNANCE_AUDIT_WARN_RECORDS"
BYTES_ENV = "NOTEBOOK2API_GOVERNANCE_AUDIT_WARN_BYTES"


def _threshold(environ, name):
    try:
        value = int(float(str(environ.get(name, "")).strip()))
    except ValueError:
        return None
    return value if value > 0 else None


def database_path(environ=None) -> str:
    environ = os.environ if environ is None else environ
    return environ.get("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", "data/notebook2api.db")


def storage_stats(path, environ=None, now=None) -> dict:
    """{records, bytes, oldest_record_at, oldest_record_age_days, thresholds, state, warnings}.
    `state` is "ok" or "warning"; a missing database is simply empty."""
    environ = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    path = Path(path)
    records, oldest = 0, None
    if path.is_file():
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            records, oldest = connection.execute("SELECT COUNT(*), MIN(occurred_at) FROM audit_records").fetchone()
        finally:
            connection.close()
    size = sum(p.stat().st_size for p in (path, path.with_name(path.name + "-wal")) if p.is_file())
    age_days = None
    if oldest:
        try:
            moment = datetime.fromisoformat(oldest)
            moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
            age_days = round(max((now - moment).total_seconds(), 0) / 86400, 2)
        except ValueError:
            pass
    limit_records, limit_bytes = _threshold(environ, RECORDS_ENV), _threshold(environ, BYTES_ENV)
    warnings = []
    if limit_records is not None and records >= limit_records:
        warnings.append(f"the audit trail holds {records} records (warning threshold {limit_records})")
    if limit_bytes is not None and size >= limit_bytes:
        warnings.append(f"the audit database is {size} bytes (warning threshold {limit_bytes})")
    result = {
        "records": records, "bytes": size, "oldest_record_at": oldest, "oldest_record_age_days": age_days,
        "thresholds": {"records": limit_records, "bytes": limit_bytes},
        "state": "warning" if warnings else "ok", "warnings": warnings,
    }
    if warnings:
        result["recommendation"] = (
            "Archive old records with `audit-db archive --keep-latest <N> --output <file>` "
            "(or --older-than-days <D>), which verifies the archive before pruning."
        )
    return result
