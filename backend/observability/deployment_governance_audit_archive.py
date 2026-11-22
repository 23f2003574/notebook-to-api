"""Archive and prune old governance audit records without breaking the chain.

`archive_and_prune` writes records 1..N (those still stored) to a JSON Lines archive, verifies that
archive on its own, and only then deletes them in one transaction that also stores the hash of record
N as a checkpoint. Verification of the retained database starts from that checkpoint, so a rewritten
boundary record is detected; in keyed mode the checkpoint carries an HMAC tag as well. Nothing is
deleted until the archive is durably on disk and verified, and an interrupted run leaves the database
untouched (a retry simply writes the archive again).

Archive format (one JSON object per line): a header, the records in sequence order (each with its
stored `mac`), and a footer holding the SHA-256 of every preceding line (and an HMAC tag in keyed
mode). `verify_archive` needs no database."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .deployment_governance_audit import (
    GENESIS_HASH,
    GovernanceAuditService,
    _compute_record_hash,
    audit_keyring_from_env,
)
from .deployment_governance_audit_backup import EXIT_ERROR, EXIT_INVALID, EXIT_REFUSED, AuditDbError
from .deployment_governance_audit_store import GovernanceAuditStorageError, SqliteAuditStore

FORMAT = "n2a-audit-archive-v1"


def _line(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def verify_archive(path, environ=None) -> dict:
    """Check an archive file on its own: format, contiguous sequence numbers, every record's hash,
    the hash links between records and from the header's `previous_hash`, the footer digest and (when
    the archive is keyed and a key is configured) every tag. Returns {valid, count, from_sequence,
    to_sequence, previous_hash, last_hash, integrity, reason}."""
    path = Path(path)
    environ = os.environ if environ is None else environ
    keyring = audit_keyring_from_env(environ)

    def bad(reason, **extra):
        return {"valid": False, "reason": reason, "count": extra.pop("count", 0), "integrity": "unknown", **extra}

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return bad(f"{path.name} could not be read")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    try:
        parsed = [json.loads(line) for line in lines]
    except ValueError:
        return bad("the archive contains a line that is not valid JSON")
    if len(parsed) < 3 or parsed[0].get("format") != FORMAT or not parsed[-1].get("footer"):
        return bad("not an audit archive (missing header or footer)")
    header, records, footer = parsed[0], parsed[1:-1], parsed[-1]
    digest = hashlib.sha256(("\n".join(lines[:-1]) + "\n").encode("utf-8")).hexdigest()
    if footer.get("digest") != digest:
        return bad("the archive digest does not match its contents (modified or truncated)")
    expected_previous = header.get("previous_hash")
    keyed = bool(header.get("keyed"))
    unverifiable = False
    if keyed:
        tag = None if keyring is None else (keyring.check_archive(footer.get("mac", ""), digest) if footer.get("mac") else False)
        if tag is False:
            return bad("the archive failed keyed integrity", integrity="keyed")
        unverifiable = tag is None
    if header.get("count") != len(records) or footer.get("count") != len(records):
        return bad("the archive record count does not match its header")
    for offset, rec in enumerate(records):
        sequence = header["from_sequence"] + offset
        try:
            occurred = datetime.fromisoformat(rec["occurred_at"])
            expected_hash = _compute_record_hash(
                sequence=rec["sequence"], action=rec["action"], actor=rec["actor"], resource=rec["resource"],
                outcome=rec["outcome"], occurred_at=occurred, metadata=rec["metadata"], previous_hash=rec["previous_hash"],
            )
        except (KeyError, ValueError, TypeError):
            return bad(f"record {sequence} is malformed", count=offset)
        if rec["sequence"] != sequence:
            return bad(f"record sequence {rec['sequence']} where {sequence} was expected", count=offset)
        if rec["previous_hash"] != expected_previous or expected_hash != rec["record_hash"]:
            return bad(f"record {sequence} does not continue the chain or its hash does not match its fields", count=offset)
        if keyed and rec.get("mac"):
            tag = None if keyring is None else keyring.check_record(rec["mac"], rec["record_hash"])
            if tag is False:
                return bad(f"record {sequence} failed keyed integrity", integrity="keyed", count=offset)
            unverifiable = unverifiable or tag is None
        elif keyed and rec["sequence"] >= header.get("keyed_from", 0):
            return bad(f"record {sequence} is missing its integrity tag", integrity="keyed", count=offset)
        expected_previous = rec["record_hash"]
    if records[-1]["record_hash"] != header.get("last_hash") or records[-1]["sequence"] != header.get("to_sequence"):
        return bad("the archive header does not match its last record")
    return {
        "valid": True, "reason": None, "count": len(records), "from_sequence": header["from_sequence"],
        "to_sequence": header["to_sequence"], "previous_hash": header["previous_hash"], "last_hash": header["last_hash"],
        "integrity": ("keyed_unverified" if unverifiable else "keyed") if keyed else "unkeyed",
    }


def _choose_through(store, head, *, through, keep_latest, older_than_days, now):
    if sum(x is not None for x in (through, keep_latest, older_than_days)) != 1:
        raise AuditDbError("give exactly one of --through, --keep-latest or --older-than-days", EXIT_ERROR)
    if through is None and keep_latest is not None:
        if keep_latest < 1:
            raise AuditDbError("--keep-latest must be at least 1", EXIT_ERROR)
        through = head["last_sequence"] - keep_latest
    elif through is None:
        cutoff = (now - timedelta(days=older_than_days)).isoformat()
        row = store._connect().execute(
            "SELECT MAX(sequence) FROM audit_records WHERE occurred_at < ?", (cutoff,)).fetchone()
        through = row[0] or 0
    through = min(through, head["last_sequence"] - 1)  # the newest record always stays
    return through


def archive_and_prune(database, output, *, through=None, keep_latest=None, older_than_days=None,
                      dry_run=False, force=False, environ=None, now=None) -> dict:
    environ = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    database, output = Path(database), Path(output)
    if not database.is_file():
        raise AuditDbError(f"{database} does not exist")
    keyring = audit_keyring_from_env(environ)
    try:
        store = SqliteAuditStore(database, keyring=keyring)
    except GovernanceAuditStorageError as exc:
        raise AuditDbError(str(exc), EXIT_INVALID) from None
    service = GovernanceAuditService(store=store)
    verdict = service.verify_chain(record=False)
    if not verdict.valid:
        raise AuditDbError(
            f"the audit chain is broken at record {verdict.first_broken_sequence} ({verdict.reason}); "
            "refusing to archive or prune it", EXIT_INVALID)
    head = store.head()
    through = _choose_through(store, head, through=through, keep_latest=keep_latest,
                              older_than_days=older_than_days, now=now)
    first = head["purged_through"] + 1
    if through < first:
        return {"pruned": 0, "archived": 0, "dry_run": dry_run, "message": "nothing to archive"}
    if dry_run:
        return {"pruned": 0, "archived": through - first + 1, "dry_run": True,
                "from_sequence": first, "to_sequence": through}
    if output.exists() and not force:
        raise AuditDbError(f"{output} already exists (use --force to replace it)", EXIT_REFUSED)

    rows, previous_hash = [], None
    for row in store.iter_rows(with_mac=True):
        if row["sequence"] > through:
            break
        rows.append(row)
    if not rows or rows[0]["sequence"] != first or rows[-1]["sequence"] != through:
        raise AuditDbError("the stored records do not cover the requested range", EXIT_INVALID)
    previous_hash = rows[0]["previous_hash"]
    keyed_from = head["keyed_from"]
    header = {"format": FORMAT, "from_sequence": first, "to_sequence": through, "count": len(rows),
              "previous_hash": previous_hash, "last_hash": rows[-1]["record_hash"],
              "created_at": now.isoformat(), "keyed": bool(keyed_from), "keyed_from": keyed_from}
    lines = [_line(header)] + [
        _line({**{k: v for k, v in r.items() if k != "occurred_at"}, "occurred_at": r["occurred_at"].isoformat()})
        for r in rows
    ]
    digest = hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()
    footer = {"footer": True, "count": len(rows), "digest": digest}
    if keyring is not None and keyed_from:
        footer["mac"] = keyring.archive_mac(digest)
    lines.append(_line(footer))

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")
    try:
        with open(partial, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, output)
        try:
            fd = os.open(output.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass  # directory fsync is best-effort (not available everywhere)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise AuditDbError(f"could not write the archive ({type(exc).__name__}); nothing was pruned") from None

    checked = verify_archive(output, environ)  # trust the file on disk, not what was just built
    if not checked["valid"] or checked["last_hash"] != rows[-1]["record_hash"] or checked["count"] != len(rows):
        raise AuditDbError(f"the written archive failed verification ({checked['reason']}); nothing was pruned", EXIT_INVALID)
    expected_boundary = head["purged_hash"] or (GENESIS_HASH if first == 1 else previous_hash)
    if checked["previous_hash"] != expected_boundary:
        raise AuditDbError("the archive does not continue from the database's checkpoint; nothing was pruned", EXIT_INVALID)
    try:
        removed = store.prune_through(through, rows[-1]["record_hash"])
    except GovernanceAuditStorageError as exc:
        raise AuditDbError(f"{exc}; the archive was kept", EXIT_ERROR) from None
    service.record(action="audit_archive", actor="system", resource="audit_chain", outcome="success",
                   metadata={"from_sequence": first, "to_sequence": through, "count": removed,
                             "archive": output.name, "digest": digest})
    return {"pruned": removed, "archived": len(rows), "dry_run": False, "from_sequence": first,
            "to_sequence": through, "archive": str(output), "digest": digest, "checkpoint_hash": rows[-1]["record_hash"]}
