"""Authorization for the plugin HTTP endpoints, which otherwise read server directories and import
modules named by the request. Both are denied unless the operator explicitly permits them:

  NOTEBOOK2API_PLUGIN_ROOTS            os.pathsep-separated directories `source_dir` may live under
  NOTEBOOK2API_PLUGIN_MODULE_PREFIXES  comma-separated module prefixes an `entry_point` may import
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException


def _roots(environ) -> list[Path]:
    raw = environ.get("NOTEBOOK2API_PLUGIN_ROOTS", "")
    return [Path(p).resolve() for p in raw.split(os.pathsep) if p.strip()]


def resolve_source_dir(source_dir: str, environ=None) -> str:
    """The real path of `source_dir` when it is inside a permitted root; traversal and symlink escapes
    are resolved before the check. Raises HTTP 403 otherwise."""
    environ = os.environ if environ is None else environ
    roots = _roots(environ)
    if not roots:
        raise HTTPException(status_code=403, detail="plugin source directories are disabled; set NOTEBOOK2API_PLUGIN_ROOTS")
    try:
        real = Path(source_dir).resolve()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=403, detail="source_dir is outside the permitted plugin roots")
    if not any(real == root or root in real.parents for root in roots):
        raise HTTPException(status_code=403, detail="source_dir is outside the permitted plugin roots")
    return str(real)


def check_entry_point(entry_point: str, environ=None) -> None:
    """Raise HTTP 403 unless `entry_point` is (inside) a module prefix the operator permitted."""
    environ = os.environ if environ is None else environ
    prefixes = [p.strip() for p in environ.get("NOTEBOOK2API_PLUGIN_MODULE_PREFIXES", "").split(",") if p.strip()]
    if not any(entry_point == p or entry_point.startswith(p + ".") for p in prefixes):
        raise HTTPException(
            status_code=403,
            detail="plugin entry point is not permitted; set NOTEBOOK2API_PLUGIN_MODULE_PREFIXES",
        )
