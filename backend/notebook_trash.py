"""A trash area for soft-deleted notebooks.

An entry is one directory, `<trash_root>/<id>/`, holding `meta.json` and `payload/<kind>/<relative path>`
for everything that belongs to the notebook (the file, its sidecars, version history, data files and
compiled app). Moving in and out is all-or-nothing: entries are staged under a `.tmp-` name and renamed
into place only when complete, and a failure part-way moves everything back, so neither a delete nor a
restore can leave a notebook half in the trash. Nothing here knows about HTTP."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

META = "meta.json"


class TrashError(Exception):
    """A trash operation that could not be completed (the notebook is left as it was)."""


def _now():
    return datetime.now(timezone.utc)


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _rollback(moved: list[tuple[Path, Path]]) -> None:
    for src, dst in reversed(moved):  # put each item back where it came from
        try:
            _move(dst, src)
        except OSError:
            pass


def _new_id(now) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def soft_delete(trash_root: Path, roots: dict, entries: list, meta: dict, retention_days: float, now=None) -> dict:
    """Move `entries` ([(kind, relative path)] under `roots[kind]`) into a new trash entry. Entries
    that do not exist are skipped. Returns the entry's metadata (with "trash_id" and "expires_at")."""
    now = now or _now()
    trash_id = _new_id(now)
    staging = trash_root / f".tmp-{trash_id}"
    final = trash_root / trash_id
    moved: list[tuple[Path, Path]] = []
    kept = []
    try:
        for kind, rel in entries:
            src = roots[kind] / rel
            if not (src.exists() or src.is_symlink()):
                continue
            dst = staging / "payload" / kind / rel
            _move(src, dst)
            moved.append((src, dst))
            kept.append({"kind": kind, "rel": rel})
        record = {
            **meta, "trash_id": trash_id, "entries": kept,
            "deleted_at": now.isoformat(),
            "expires_at": (now + timedelta(days=retention_days)).isoformat(),
        }
        (staging / META).write_text(json.dumps(record), encoding="utf-8")
        os.replace(staging, final)
    except Exception as exc:
        _rollback(moved)
        shutil.rmtree(staging, ignore_errors=True)
        raise TrashError(f"could not move the notebook to the trash: {exc}") from exc
    return record


def _read(entry_dir: Path):
    try:
        return json.loads((entry_dir / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_entries(trash_root: Path) -> list[dict]:
    """Complete trash entries, newest first (half-written `.tmp-` staging directories are ignored)."""
    if not trash_root.is_dir():
        return []
    found = [m for d in trash_root.iterdir() if d.is_dir() and not d.name.startswith(".") for m in [_read(d)] if m]
    return sorted(found, key=lambda m: m["deleted_at"], reverse=True)


def get_entry(trash_root: Path, trash_id: str):
    if not trash_id or "/" in trash_id or "\\" in trash_id or trash_id.startswith("."):
        return None
    return _read(trash_root / trash_id)


def restore(trash_root: Path, roots: dict, trash_id: str) -> dict:
    """Move a trash entry's items back to their original places. The caller has checked that the
    notebook's name is free; an item whose destination exists anyway aborts the restore with
    nothing moved (except the optional app, which is dropped if its directory is occupied)."""
    meta = get_entry(trash_root, trash_id)
    if meta is None:
        raise KeyError(trash_id)
    base = trash_root / trash_id / "payload"
    plan = []
    for item in meta["entries"]:
        src = base / item["kind"] / item["rel"]
        dst = roots[item["kind"]] / item["rel"]
        if dst.exists() or dst.is_symlink():
            if item["kind"] == "apps":
                continue  # a compiled app can be rebuilt; never block the notebook on it
            raise TrashError(f"{item['rel']} already exists")
        plan.append((dst, src))  # (destination, current location): _rollback moves back dst <- ... reversed below
    moved: list[tuple[Path, Path]] = []
    try:
        for dst, src in plan:
            _move(src, dst)
            moved.append((src, dst))
    except Exception as exc:
        for src, dst in reversed(moved):  # undo: dst back to the trash payload
            try:
                _move(dst, src)
            except OSError:
                pass
        raise TrashError(f"could not restore the notebook: {exc}") from exc
    shutil.rmtree(trash_root / trash_id, ignore_errors=True)
    return meta


def purge(trash_root: Path, trash_id: str) -> bool:
    if get_entry(trash_root, trash_id) is None:
        return False
    shutil.rmtree(trash_root / trash_id)
    return True


def purge_expired(trash_root: Path, now=None, everything: bool = False) -> list[str]:
    """Permanently remove entries past their expiry (all of them with `everything`); returns the ids."""
    now = now or _now()
    gone = []
    for meta in list_entries(trash_root):
        try:
            expired = datetime.fromisoformat(meta["expires_at"]) <= now
        except (KeyError, ValueError):
            expired = True
        if everything or expired:
            shutil.rmtree(trash_root / meta["trash_id"], ignore_errors=True)
            gone.append(meta["trash_id"])
    return gone
