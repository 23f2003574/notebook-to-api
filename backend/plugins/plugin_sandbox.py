from __future__ import annotations

import builtins
import os
import resource
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

_HAS_RUSAGE_THREAD = hasattr(resource, "RUSAGE_THREAD")

# Guards process-wide monkeypatching of builtins.open (see _restrict_filesystem).
# builtins.open is global state, not thread-local, so only one
# filesystem-restricted execution may be in flight across the whole process
# at a time; this lock serializes them rather than letting them corrupt
# each other's restrictions.
_FILESYSTEM_PATCH_LOCK = threading.Lock()


class SandboxAlreadyExistsError(ValueError):
    pass


class UnknownSandboxError(KeyError):
    pass


class SandboxTimeoutError(TimeoutError):
    pass


class SandboxResourceLimitExceededError(RuntimeError):
    pass


class SandboxFilesystemViolationError(PermissionError):
    pass


class SandboxBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxPolicy:
    """Resource and access limits applied to a plugin's sandboxed execution.

    ``timeout_seconds`` and ``max_cpu_seconds`` are genuinely enforced (see
    :meth:`PluginSandbox.execute`). ``max_memory_mb`` is recorded and
    validated but is NOT enforced: reliably capping memory for one thread
    within a shared process without affecting the rest of the host would
    require OS-level isolation (a subprocess, cgroup, or container), which
    is out of scope for this in-process, thread-based sandbox. This sandbox
    is best-effort isolation for cooperative plugins, not a hard security
    boundary against adversarial native code.
    """

    timeout_seconds: float = 5.0
    max_cpu_seconds: Optional[float] = None
    max_memory_mb: Optional[float] = None
    allowed_paths: tuple = ()

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_cpu_seconds is not None and self.max_cpu_seconds <= 0:
            raise ValueError("max_cpu_seconds must be positive")
        if self.max_memory_mb is not None and self.max_memory_mb <= 0:
            raise ValueError("max_memory_mb must be positive")

    def to_dict(self) -> dict:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_cpu_seconds": self.max_cpu_seconds,
            "max_memory_mb": self.max_memory_mb,
            "allowed_paths": list(self.allowed_paths),
        }


@dataclass(frozen=True)
class Sandbox:
    """A created isolation context for a single plugin."""

    plugin: str
    policy: SandboxPolicy
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "plugin": self.plugin,
            "policy": self.policy.to_dict(),
            "created_at": self.created_at.isoformat(),
        }


@contextmanager
def _restrict_filesystem(allowed_paths: tuple):
    resolved_allowed = [str(Path(path).resolve()) for path in allowed_paths]
    original_open = builtins.open

    def restricted_open(file, *args, **kwargs):
        try:
            target = str(Path(file).resolve())
        except TypeError:
            target = None
        if target is not None and not any(
            target == allowed or target.startswith(allowed + os.sep) for allowed in resolved_allowed
        ):
            raise SandboxFilesystemViolationError(f"access to '{file}' is not permitted by sandbox policy")
        return original_open(file, *args, **kwargs)

    builtins.open = restricted_open
    try:
        yield
    finally:
        builtins.open = original_open


class PluginSandbox:
    """Runs plugin code with a timeout, CPU budget, and filesystem allowlist.

    Isolation is achieved with a worker thread plus a monkeypatched
    ``builtins.open`` and per-thread CPU accounting (``RUSAGE_THREAD``) —
    real, testable mechanisms for cooperative code, but not a substitute for
    OS-level sandboxing against actively hostile plugins.
    """

    def __init__(self) -> None:
        self._sandboxes: dict = {}
        self._lock = threading.Lock()

    def create(self, plugin: str, policy: Optional[SandboxPolicy] = None) -> Sandbox:
        policy = policy or SandboxPolicy()
        with self._lock:
            entry = self._sandboxes.get(plugin)
            if entry is not None and not entry["destroyed"]:
                raise SandboxAlreadyExistsError(plugin)
            sandbox = Sandbox(plugin=plugin, policy=policy, created_at=datetime.now(timezone.utc))
            self._sandboxes[plugin] = {"sandbox": sandbox, "destroyed": False, "execution_count": 0}
        return sandbox

    def destroy(self, plugin: str) -> None:
        with self._lock:
            entry = self._sandboxes.get(plugin)
            if entry is None or entry["destroyed"]:
                raise UnknownSandboxError(plugin)
            entry["destroyed"] = True

    def status(self, plugin: str) -> dict:
        with self._lock:
            entry = self._sandboxes.get(plugin)
        if entry is None:
            raise UnknownSandboxError(plugin)
        return {
            "plugin": plugin,
            "status": "destroyed" if entry["destroyed"] else "active",
            "created_at": entry["sandbox"].created_at.isoformat(),
            "execution_count": entry["execution_count"],
            "policy": entry["sandbox"].policy.to_dict(),
        }

    def execute(self, plugin: str, func: Callable, *args, **kwargs):
        with self._lock:
            entry = self._sandboxes.get(plugin)
        if entry is None or entry["destroyed"]:
            raise UnknownSandboxError(plugin)
        policy = entry["sandbox"].policy

        result_box: dict = {}
        error_box: dict = {}
        cpu_seconds_box: dict = {}

        def run() -> None:
            try:
                start = resource.getrusage(resource.RUSAGE_THREAD) if _HAS_RUSAGE_THREAD else None
                if policy.allowed_paths:
                    if not _FILESYSTEM_PATCH_LOCK.acquire(timeout=policy.timeout_seconds):
                        error_box["error"] = SandboxBusyError(
                            "sandbox filesystem restriction is in use by another execution"
                        )
                        return
                    try:
                        with _restrict_filesystem(policy.allowed_paths):
                            result_box["result"] = func(*args, **kwargs)
                    finally:
                        _FILESYSTEM_PATCH_LOCK.release()
                else:
                    result_box["result"] = func(*args, **kwargs)
                if start is not None:
                    end = resource.getrusage(resource.RUSAGE_THREAD)
                    cpu_seconds_box["cpu_seconds"] = (end.ru_utime + end.ru_stime) - (
                        start.ru_utime + start.ru_stime
                    )
            except BaseException as exc:  # noqa: BLE001 - re-raised in the caller thread below
                error_box["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout=policy.timeout_seconds)
        if thread.is_alive():
            raise SandboxTimeoutError(
                f"execution of '{plugin}' exceeded {policy.timeout_seconds}s timeout"
            )

        if "error" in error_box:
            raise error_box["error"]

        cpu_seconds = cpu_seconds_box.get("cpu_seconds")
        if policy.max_cpu_seconds is not None and cpu_seconds is not None:
            if cpu_seconds > policy.max_cpu_seconds:
                raise SandboxResourceLimitExceededError(
                    f"execution of '{plugin}' used {cpu_seconds:.3f}s CPU, "
                    f"exceeding limit of {policy.max_cpu_seconds}s"
                )

        with self._lock:
            entry["execution_count"] += 1

        return result_box.get("result")


_plugin_sandbox = PluginSandbox()


def get_plugin_sandbox() -> PluginSandbox:
    return _plugin_sandbox


def get_plugin_loader_dependency():
    """Resolves the active PluginLoader for the sandbox execute endpoint.

    This indirection (rather than importing get_plugin_loader directly at
    module scope) breaks an import cycle with plugin_loader.py, which
    imports this module to route imports through a sandbox. It must be a
    distinct, importable, overridable callable — FastAPI's
    dependency_overrides keys on the exact object passed to Depends(), so a
    test overriding get_plugin_loader alone would not affect this endpoint.
    """
    from .plugin_loader import get_plugin_loader

    return get_plugin_loader()


router = APIRouter(prefix="/plugins/sandbox", tags=["plugins-sandbox"])


@router.post("", status_code=201)
def create_sandbox_endpoint(
    payload: dict = Body(default={}),
    sandbox: PluginSandbox = Depends(get_plugin_sandbox),
) -> dict:
    plugin = payload.get("plugin", "")
    if not plugin:
        raise HTTPException(status_code=422, detail="plugin is required")
    policy_payload = payload.get("policy") or {}
    try:
        policy = SandboxPolicy(
            timeout_seconds=policy_payload.get("timeout_seconds", 5.0),
            max_cpu_seconds=policy_payload.get("max_cpu_seconds"),
            max_memory_mb=policy_payload.get("max_memory_mb"),
            allowed_paths=tuple(policy_payload.get("allowed_paths", ())),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        created = sandbox.create(plugin, policy)
    except SandboxAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=f"sandbox for '{exc}' already exists")
    return created.to_dict()


@router.post("/{plugin}/execute")
def execute_sandbox_endpoint(
    plugin: str,
    payload: dict = Body(default={}),
    sandbox: PluginSandbox = Depends(get_plugin_sandbox),
    loader=Depends(get_plugin_loader_dependency),
) -> dict:
    loaded = loader.get_loaded(plugin)
    if loaded is None:
        raise HTTPException(status_code=404, detail="plugin is not loaded")
    entry_point = getattr(loaded.module, "main", None)
    if entry_point is None or not callable(entry_point):
        raise HTTPException(status_code=422, detail="plugin does not expose a callable 'main' entry point")
    try:
        result = sandbox.execute(plugin, entry_point, **payload.get("kwargs", {}))
    except UnknownSandboxError:
        raise HTTPException(status_code=404, detail="no sandbox exists for plugin")
    except SandboxTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except SandboxResourceLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except SandboxFilesystemViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return {"plugin": plugin, "result": result}


@router.delete("/{plugin}", status_code=204)
def destroy_sandbox_endpoint(
    plugin: str,
    sandbox: PluginSandbox = Depends(get_plugin_sandbox),
) -> None:
    try:
        sandbox.destroy(plugin)
    except UnknownSandboxError:
        raise HTTPException(status_code=404, detail="no sandbox exists for plugin")


@router.get("/{plugin}")
def get_sandbox_status_endpoint(
    plugin: str,
    sandbox: PluginSandbox = Depends(get_plugin_sandbox),
) -> dict:
    try:
        return sandbox.status(plugin)
    except UnknownSandboxError:
        raise HTTPException(status_code=404, detail="no sandbox exists for plugin")
