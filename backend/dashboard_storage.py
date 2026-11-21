"""Is the dashboard's notebook storage durable?

The dashboard keeps uploaded notebooks, their saved versions and data files, compile and deploy
history and compiled apps on disk (NOTEBOOK_API_UPLOAD_DIR, NOTEBOOK_API_GENERATED_DIR,
NOTEBOOK_API_GENERATED_APPS_DIR). In a container those directories are lost with the container unless
they sit on a mounted volume. This reports, without exposing any path, whether they do -- the
counterpart of the governance audit storage check, and held to the same rule: durability is never
claimed for storage that is ephemeral."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

SETTINGS = {
    "uploads": "NOTEBOOK_API_UPLOAD_DIR",
    "generated": "NOTEBOOK_API_GENERATED_DIR",
    "generated_apps": "NOTEBOOK_API_GENERATED_APPS_DIR",
}
ASSERT_VARIABLE = "NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT"


def _in_container(environ) -> bool:
    return bool(environ.get("KUBERNETES_SERVICE_HOST")) or os.path.exists("/.dockerenv")


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def notebook_storage_status(directories: dict, environ=None) -> dict:
    """{"durable", "production", "issues"} for `directories` ({"uploads": path, ...}). Durable only
    when none of them is in a temporary directory or on a container's ephemeral filesystem. Set
    NOTEBOOK_API_STORAGE_VOLUME_PERSISTENT=1 to assert that the data directory is a mounted volume
    (the generated compose and Kubernetes files do)."""
    environ = os.environ if environ is None else environ
    production = environ.get("NOTEBOOK2API_ENVIRONMENT", "").strip().lower() in {"production", "prod"}
    issues: list[str] = []
    if not _truthy(environ.get(ASSERT_VARIABLE)):
        tmp_roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp"), Path("/var/tmp")}
        container = _in_container(environ)
        try:
            root_device = os.stat("/").st_dev
        except OSError:
            root_device = None
        for name, directory in directories.items():
            path = Path(directory).resolve()
            if any(root == path or root in path.parents for root in tmp_roots):
                issues.append(f"{name} are in a temporary directory ({SETTINGS[name]})")
            elif container and root_device is not None:
                probe = path
                while not probe.exists() and probe != probe.parent:
                    probe = probe.parent
                try:
                    if os.stat(probe).st_dev == root_device:
                        issues.append(
                            f"{name} are on the container's ephemeral filesystem; mount a volume there "
                            f"and set {ASSERT_VARIABLE}=1 ({SETTINGS[name]})"
                        )
                except OSError:
                    pass
    return {"durable": not issues, "production": production, "issues": issues}
