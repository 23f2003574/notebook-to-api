"""Dashboard API keys managed at runtime (create, rotate, revoke) without a restart.

Keys from NOTEBOOK_API_DASHBOARD_KEY(S)/_KEYS_FILE keep working unchanged and take precedence over
a stored key of the same name. Managed keys live in a small SQLite database
(NOTEBOOK_API_DASHBOARD_KEYS_DB, default dashboard_keys.db beside the governance audit database).
Only the SHA-256 digest of a key is stored; the secret is returned once, by the request that
creates or rotates it. Revoking, expiring or rotating a key takes effect on the next request
because authentication reads the store every time. The routes need the admin scope (see
dashboard.required_scope). Authentication is by the X-API-Key header, never a cookie, so there is
no ambient credential for a cross-site request to ride on."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SCOPES = ("read", "compile", "admin")
router = APIRouter()


def database_path() -> Path:
    configured = os.getenv("NOTEBOOK_API_DASHBOARD_KEYS_DB", "").strip()
    if configured:
        return Path(configured)
    audit = os.getenv("NOTEBOOK2API_GOVERNANCE_DATABASE_PATH") or "data/notebook2api.db"
    return Path(audit).parent / "dashboard_keys.db"


_READY: set = set()  # databases whose tables exist, so a request does not repeat the DDL
LAST_USED_INTERVAL_SECONDS = 60.0
_LAST_TOUCH: dict = {}


def _connect(create: bool = False):
    path = database_path()
    exists = path.exists()
    if not exists:
        _READY.discard(str(path))
        if not create:
            return None
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # transactions are explicit (see _audited_change)
    conn.row_factory = sqlite3.Row
    if str(path) not in _READY:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS dashboard_keys (name TEXT PRIMARY KEY, digest TEXT NOT NULL, scope TEXT NOT NULL,"
            " expires TEXT, created_at TEXT NOT NULL, rotated_at TEXT, revoked_at TEXT, last_used_at TEXT)"
        )
        if "last_used_at" not in {r["name"] for r in conn.execute("PRAGMA table_info(dashboard_keys)")}:
            conn.execute("ALTER TABLE dashboard_keys ADD COLUMN last_used_at TEXT")  # stores made before this column
        _READY.add(str(path))
    return conn


def touch_due(name: str) -> bool:
    """Whether `name`'s last use should be written now (at most once per interval per process)."""
    import time

    return time.monotonic() - _LAST_TOUCH.get(name, -1e9) >= LAST_USED_INTERVAL_SECONDS


def touch(name: str) -> None:
    """Record that managed key `name` authenticated a request (no-op for keys from the environment)."""
    import time

    _LAST_TOUCH[name] = time.monotonic()
    try:
        conn = _connect()
        if conn is not None:
            with closing(conn):
                conn.execute("UPDATE dashboard_keys SET last_used_at=? WHERE name=? AND revoked_at IS NULL", (_now(), name))
    except sqlite3.Error:
        logging.getLogger("uvicorn.error").warning("could not record the last use of a dashboard key")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _new_secret() -> str:
    return "n2a_" + secrets.token_urlsafe(32)


def list_keys() -> list[dict]:
    """Metadata of every managed key (never a secret or digest)."""
    conn = _connect()
    if conn is None:
        return []
    with closing(conn):
        rows = conn.execute("SELECT * FROM dashboard_keys ORDER BY name").fetchall()
    return [{k: row[k] for k in ("name", "scope", "expires", "created_at", "rotated_at", "revoked_at", "last_used_at")} for row in rows]


def active_credentials() -> list[tuple[str, bytes, str, date | None]]:
    """(name, digest, scope, expiry) of every key that has not been revoked."""
    conn = _connect()
    if conn is None:
        return []
    with closing(conn):
        rows = conn.execute("SELECT * FROM dashboard_keys WHERE revoked_at IS NULL").fetchall()
    return [(r["name"], bytes.fromhex(r["digest"]), r["scope"], date.fromisoformat(r["expires"]) if r["expires"] else None)
            for r in rows]


def _record_audit(action: str, name: str, actor: str, **metadata) -> None:
    from backend.observability.deployment_governance_api import _build_audit_service

    _build_audit_service().record(
        action=action, actor=actor, resource=f"dashboard-key:{name}", outcome="success", metadata=metadata)


@contextmanager
def _audited_change(conn, action: str, name: str, actor: str, **metadata):
    """Make a key change and its audit record succeed or fail together.

    The key database and the audit database are separate SQLite files, so no single transaction spans
    them. The key change is made inside an open (uncommitted) transaction, the audit record is
    written next, and only then is the key change committed: if the audit record cannot be written the
    key change is rolled back and the request fails with 503, so a change never exists without its
    record. (If the final commit itself fails after the record was written, the audit trail holds an
    extra entry for a change that did not happen, and an `.aborted` entry is attempted; the trail errs
    towards over-reporting, never towards a missing record.)"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        _record_audit(action, name, actor, **metadata)
    except (HTTPException, sqlite3.IntegrityError):
        conn.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - audit or key store failure: undo the change
        conn.rollback()
        logging.getLogger("uvicorn.error").error("dashboard key change %s refused: %s", action, type(exc).__name__)
        raise HTTPException(503, "The change was not made because its audit record could not be written") from None
    try:
        conn.commit()
    except Exception:  # noqa: BLE001
        conn.rollback()
        try:
            _record_audit(action + ".aborted", name, actor)
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(503, "The change could not be committed") from None


def _actor(request: Request) -> str:
    return getattr(request.state, "dashboard_actor", None) or "dashboard"


def _validate_name(name) -> str:
    if not isinstance(name, str) or not NAME.match(name):
        raise HTTPException(400, "name must be 1-64 characters of letters, digits and . _ -")
    return name


@router.get("/api/keys")
def keys_index():
    return {"keys": list_keys()}


@router.post("/api/keys", status_code=201)
def keys_create(data: dict, request: Request):
    from backend import dashboard

    name = _validate_name(data.get("name"))
    scope = data.get("scope", "read")
    if scope not in SCOPES:
        raise HTTPException(400, f"scope must be one of: {', '.join(SCOPES)}")
    expires = data.get("expires")
    if expires is not None:
        try:
            expires = date.fromisoformat(str(expires))
        except ValueError:
            raise HTTPException(400, "expires must be a date, YYYY-MM-DD") from None
        if expires < dashboard._utc_today():
            raise HTTPException(400, "expires must not be in the past")
        expires = expires.isoformat()
    configured = {n for n, *_ in dashboard.configured_dashboard_keys()}
    secret = _new_secret()
    with closing(_connect(create=True)) as conn:
        if name in configured:
            raise HTTPException(409, "a key with this name is configured through the environment")
        try:
            with _audited_change(conn, "dashboard.key_created", name, _actor(request), scope=scope, expires=expires):
                conn.execute("INSERT INTO dashboard_keys (name, digest, scope, expires, created_at) VALUES (?,?,?,?,?)",
                             (name, _digest(secret), scope, expires, _now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "a key with this name already exists") from None
    return {"name": name, "scope": scope, "expires": expires, "key": secret,
            "notice": "Store this key now; it cannot be shown again."}


@router.patch("/api/keys/{name}")
def keys_update(name: str, data: dict, request: Request):
    """Change a managed key's scope and/or expiry (`expires: null` removes the expiry). Takes effect on
    the next request; the secret is untouched and never returned."""
    from backend import dashboard

    changes = {}
    if "scope" in data:
        if data["scope"] not in SCOPES:
            raise HTTPException(400, f"scope must be one of: {', '.join(SCOPES)}")
        changes["scope"] = data["scope"]
    if "expires" in data:
        expires = data["expires"]
        if expires is not None:
            try:
                expires = date.fromisoformat(str(expires))
            except ValueError:
                raise HTTPException(400, "expires must be a date, YYYY-MM-DD") from None
            if expires < dashboard._utc_today():
                raise HTTPException(400, "expires must not be in the past")
            expires = expires.isoformat()
        changes["expires"] = expires
    if not changes:
        raise HTTPException(400, "send scope and/or expires")
    with closing(_connect(create=True)) as conn:
        with _audited_change(conn, "dashboard.key_updated", name, _actor(request), **changes):
            sets = ", ".join(f"{column}=?" for column in changes)
            changed = conn.execute(f"UPDATE dashboard_keys SET {sets} WHERE name=? AND revoked_at IS NULL",
                                   (*changes.values(), name)).rowcount
            if not changed:
                raise HTTPException(404, "no active managed key with this name")
    return {"name": name, **changes}


@router.post("/api/keys/{name}/rotate")
def keys_rotate(name: str, request: Request):
    secret = _new_secret()
    with closing(_connect(create=True)) as conn:
        with _audited_change(conn, "dashboard.key_rotated", name, _actor(request)):
            changed = conn.execute(
                "UPDATE dashboard_keys SET digest=?, rotated_at=? WHERE name=? AND revoked_at IS NULL",
                (_digest(secret), _now(), name)).rowcount
            if not changed:
                raise HTTPException(404, "no active managed key with this name")
    return {"name": name, "key": secret, "notice": "The previous key no longer works. Store this one now."}


@router.delete("/api/keys/{name}")
def keys_revoke(name: str, request: Request):
    with closing(_connect(create=True)) as conn:
        with _audited_change(conn, "dashboard.key_revoked", name, _actor(request)):
            changed = conn.execute("UPDATE dashboard_keys SET revoked_at=? WHERE name=? AND revoked_at IS NULL",
                                   (_now(), name)).rowcount
            if not changed:
                raise HTTPException(404, "no active managed key with this name")
    return {"name": name, "revoked": True}
