"""Back up, verify and restore the governance audit database.

Backups use SQLite's online backup API, so they are consistent while the dashboard is writing.
A backup is verified (hash chain and, when a key is configured, keyed integrity) before it is
trusted, and a restore never replaces a database silently: the replaced file is kept as .bak, a
database holding newer or different records needs --force, and the swap is one atomic rename.
Messages never contain absolute paths beyond what the operator typed, and never a key."""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from .deployment_governance_audit import GovernanceAuditService, audit_keyring_from_env
from .deployment_governance_audit_store import GovernanceAuditStorageError, SqliteAuditStore

EXIT_OK, EXIT_ERROR, EXIT_REFUSED, EXIT_INVALID = 0, 1, 2, 3


class AuditDbError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code


def _online_copy(source: Path, destination: Path) -> None:
    """Consistent copy of a live SQLite database (works while writers are active)."""
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
        try:
            dst = sqlite3.connect(destination)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error as exc:
        raise AuditDbError(f"could not read the audit database {source} ({type(exc).__name__})") from None


def _require_audit_database(path: Path) -> None:
    if not path.is_file():
        raise AuditDbError(f"{path} does not exist")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.Error:
        raise AuditDbError(f"{path} is not a SQLite database") from None
    if not {"audit_records", "audit_meta"} <= tables:
        raise AuditDbError(f"{path} is not a governance audit database")


def verify_file(path, environ=None) -> dict:
    """Verify an audit database file without modifying it (a temporary copy is opened, so older
    schemas are migrated in the copy only). Returns {valid, checked, integrity, ...}."""
    path = Path(path)
    _require_audit_database(path)
    environ = os.environ if environ is None else environ
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "audit.db"
        _online_copy(path, copy)
        try:
            store = SqliteAuditStore(copy, keyring=audit_keyring_from_env(environ))
        except GovernanceAuditStorageError as exc:
            raise AuditDbError(str(exc), EXIT_INVALID) from None
        service = GovernanceAuditService(store=store)
        result = service.verify_chain(record=False)
        head = service.head()
        return {
            "valid": result.valid, "checked": result.checked, "integrity": result.integrity,
            "first_broken_sequence": result.first_broken_sequence, "reason": result.reason,
            "last_sequence": head["last_sequence"], "last_hash": head["last_hash"],
        }


def backup(database, output, *, force=False, environ=None) -> dict:
    database, output = Path(database), Path(output)
    _require_audit_database(database)
    if output.exists() and not force:
        raise AuditDbError(f"{output} already exists (use --force to replace it)", EXIT_REFUSED)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")
    partial.unlink(missing_ok=True)
    try:
        _online_copy(database, partial)
        report = verify_file(partial, environ)
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    return {**report, "backup": str(output)}


def _head_of(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT last_sequence, last_hash, purged_through FROM audit_meta WHERE id = 1").fetchone()
        return {"last_sequence": row[0], "last_hash": row[1], "purged_through": row[2]}
    finally:
        conn.close()


def _hash_at(path: Path, sequence: int):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT record_hash FROM audit_records WHERE sequence = ?", (sequence,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def restore(backup_file, database, *, force=False, environ=None) -> dict:
    backup_file, database = Path(backup_file), Path(database)
    report = verify_file(backup_file, environ)  # raises when it is not an audit database
    if not report["valid"]:
        raise AuditDbError(
            f"the backup failed verification ({report['reason']}); nothing was changed", EXIT_INVALID)
    replaced = None
    if database.exists():
        _require_audit_database(database)
        current, incoming = _head_of(database), _head_of(backup_file)
        if not force:
            if current["last_sequence"] > incoming["last_sequence"]:
                raise AuditDbError(
                    f"the database holds newer records (sequence {current['last_sequence']}) than the "
                    f"backup (sequence {incoming['last_sequence']}); restoring would lose them. "
                    "Use --force to replace it anyway", EXIT_REFUSED)
            if current["last_sequence"] > incoming["purged_through"]:
                same = _hash_at(backup_file, current["last_sequence"])
                if same is not None and same != current["last_hash"]:
                    raise AuditDbError(
                        "the database and the backup disagree about the same record (their chains have "
                        "diverged). Use --force to replace it anyway", EXIT_REFUSED)
        replaced = database.with_name(database.name + ".bak")
        if replaced.exists():
            replaced = database.with_name(f"{database.name}.{time.strftime('%Y%m%dT%H%M%S')}.bak")
        _online_copy(database, replaced)
    database.parent.mkdir(parents=True, exist_ok=True)
    staged = database.with_name(database.name + ".restore.tmp")
    staged.unlink(missing_ok=True)
    try:
        _online_copy(backup_file, staged)
        # Dashboard must be stopped: a running process holds the old file open and would keep
        # writing to it. The rename is atomic; stale WAL files of the old database are removed.
        os.replace(staged, database)
    finally:
        staged.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    after = verify_file(database, environ)
    return {**after, "restored_from": str(backup_file), "previous_database": str(replaced) if replaced else None}


def run_cli(args) -> int:
    """Entry point for `audit-db`: returns the process exit code (0 ok, 1 error, 2 refused,
    3 invalid backup or chain)."""
    import json

    database = getattr(args, "database", None) or os.environ.get(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH", "data/notebook2api.db")
    if args.audit_db_command in ("archive", "verify-archive"):
        return _run_archive_cli(args, database)
    if args.audit_db_command == "status":
        return _run_status_cli(args, database)
    try:
        if args.audit_db_command == "backup":
            result = backup(database, args.output, force=args.force)
        elif args.audit_db_command == "restore":
            result = restore(args.input, database, force=args.force)
        else:
            result = verify_file(args.file)
    except AuditDbError as exc:
        print(f"error: {exc}")
        return exc.code
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        verdict = "intact" if result["valid"] else f"BROKEN at record {result['first_broken_sequence']}: {result['reason']}"
        print(f"{args.audit_db_command}: chain {verdict} ({result['checked']} records, integrity {result['integrity']}, "
              f"head sequence {result['last_sequence']})")
        for key, label in (("backup", "backup written to"), ("restored_from", "restored from"),
                           ("previous_database", "previous database kept as")):
            if result.get(key):
                print(f"{label} {result[key]}")
        if args.audit_db_command == "restore":
            print("Restart the dashboard so it opens the restored database.")
    return EXIT_OK if result["valid"] else EXIT_INVALID


def _run_status_cli(args, database) -> int:
    """Record count, size and age of the audit database with threshold warnings (read-only). A
    warning still exits 0: the store works; the recommendation says how to shrink it."""
    import json

    from .deployment_governance_audit_storage import storage_stats

    try:
        stats = storage_stats(database)
    except Exception as exc:  # noqa: BLE001
        print(f"error: the audit database could not be read ({type(exc).__name__})")
        return EXIT_ERROR
    if args.json:
        print(json.dumps(stats, indent=2, sort_keys=True))
        return EXIT_OK
    age = stats["oldest_record_age_days"]
    print(f"audit database: {stats['records']} records, {stats['bytes']} bytes"
          + (f", oldest record {age} days old" if age is not None else ", empty"))
    for warning in stats["warnings"]:
        print(f"WARNING: {warning}")
    if stats.get("recommendation"):
        print(stats["recommendation"])
    return EXIT_OK


def _run_archive_cli(args, database) -> int:
    import json

    from .deployment_governance_audit_archive import archive_and_prune, verify_archive

    try:
        if args.audit_db_command == "verify-archive":
            result = verify_archive(args.file)
        else:
            result = archive_and_prune(
                database, args.output, through=args.through, keep_latest=args.keep_latest,
                older_than_days=args.older_than_days, dry_run=args.dry_run, force=args.force)
    except AuditDbError as exc:
        print(f"error: {exc}")
        return exc.code
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.audit_db_command == "verify-archive":
        print("archive intact: records %s-%s (%s, integrity %s)" % (result["from_sequence"], result["to_sequence"], result["count"], result["integrity"])
              if result["valid"] else f"archive INVALID: {result['reason']}")
    elif result.get("dry_run"):
        print(f"dry run: would archive and prune {result.get('archived', 0)} records; nothing changed")
    else:
        print(f"archived {result['archived']} records to {result.get('archive', '-')}; pruned {result['pruned']}"
              if result["pruned"] else "nothing to archive")
    return EXIT_OK if result.get("valid", True) else EXIT_INVALID
