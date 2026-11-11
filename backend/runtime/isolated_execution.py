"""Process-isolated callable execution with enforceable timeouts."""
from __future__ import annotations

import json
import logging
import math
import multiprocessing as mp
import os
import signal
import threading
import time
import uuid
from typing import Any, Callable, Mapping, Sequence


_LOG = logging.getLogger("uvicorn.error")  # same logger the generated app uses


def _emit_event(event: str, execution_id: str, started: float, **fields: Any) -> None:
    """Log one structured event. Only fixed, non-sensitive fields are ever
    included (ids, timings, outcome, exit code) -- never arguments, results,
    source, or exception text. A logging failure is swallowed so it can never
    break execution or cleanup."""
    try:
        record = {"event": event, "execution_id": execution_id,
                  "elapsed_ms": round((time.monotonic() - started) * 1000, 1), **fields}
        _LOG.info("isolated_execution %s", json.dumps(record, sort_keys=True))
    except Exception:  # noqa: BLE001
        pass


class IsolatedExecutionError(RuntimeError):
    """Raised when the child process fails, crashes, or cannot return a result."""


class IsolatedExecutionTimeout(IsolatedExecutionError):
    """Raised when the child process exceeds its timeout and is terminated."""


def _context() -> mp.context.BaseContext:
    methods = mp.get_all_start_methods()
    return mp.get_context("fork" if "fork" in methods else "spawn")


def _apply_memory_limit(limit_mb: float) -> None:
    """Cap the child's address space (RLIMIT_AS) before any notebook code runs.

    POSIX only; the OS refuses allocations beyond the cap, so the child gets a
    MemoryError instead of growing unbounded. The cap covers the whole address
    space inherited from the parent, so pick a value above the app's baseline.
    Some platforms (notably macOS) do not enforce RLIMIT_AS.
    """
    import resource

    limit = int(limit_mb * 1024 * 1024)
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    resource.setrlimit(resource.RLIMIT_AS, (limit, hard))


def _apply_cpu_limit(limit_seconds: float) -> None:
    """Cap the child's CPU time (RLIMIT_CPU), not wall-clock time.

    Exceeding the soft limit delivers SIGXCPU, which terminates the child; the
    hard limit one second later is a SIGKILL backstop. POSIX only (idle or
    blocked time does not count; use ``timeout`` for wall-clock limits).
    """
    import math
    import resource

    soft = max(1, math.ceil(limit_seconds))
    _, hard = resource.getrlimit(resource.RLIMIT_CPU)
    new_hard = soft + 1 if hard == resource.RLIM_INFINITY else hard
    resource.setrlimit(resource.RLIMIT_CPU, (min(soft, new_hard), new_hard))


def _kill_process_group(pid: int | None) -> None:
    """SIGKILL the child's process group so subprocesses started by notebook code
    cannot outlive a timeout or crash. No-op when the child never became a group
    leader (non-POSIX, or it died before setsid)."""
    if pid is None or not hasattr(os, "killpg"):
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


_ACTIVE: dict[int, Any] = {}
_ACTIVE_LOCK = threading.Lock()


def terminate_active_processes(grace_seconds: float = 2.0) -> int:
    """Stop every isolated child still running; returns how many were signalled.

    For application shutdown: SIGTERM each child's process group, wait up to
    ``grace_seconds`` in total, SIGKILL whatever is left and reap it. Safe when
    a child already exited or is being reaped by its own ``run_isolated`` call
    (that call then reports a controlled failure and frees its capacity slot).
    """
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE.values())
    for proc in procs:
        try:
            if hasattr(os, "killpg") and proc.pid is not None:
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except (OSError, ValueError):
            pass
    deadline = time.monotonic() + max(0.0, grace_seconds)
    for proc in procs:
        try:
            proc.join(max(0.0, deadline - time.monotonic()))
            if proc.is_alive():
                _kill_process_group(proc.pid)
                proc.kill()
                proc.join(1)
            elif proc.pid is not None:
                _kill_process_group(proc.pid)  # grandchildren that outlived the leader
        except (OSError, ValueError, AssertionError):
            pass  # already reaped and closed by run_isolated
    return len(procs)


def _child(
    conn: Any,
    func: Callable[..., Any],
    args: Sequence[Any],
    kwargs: Mapping[str, Any],
    memory_limit_mb: float | None = None,
    cpu_limit_seconds: float | None = None,
) -> None:
    try:
        try:
            if hasattr(os, "setsid"):
                try:
                    os.setsid()  # own process group so the parent can reap grandchildren
                except OSError:
                    pass
            if memory_limit_mb:
                _apply_memory_limit(memory_limit_mb)
            if cpu_limit_seconds:
                _apply_cpu_limit(cpu_limit_seconds)
            conn.send(("ok", func(*args, **kwargs)))
        except MemoryError:
            conn.send(("error", "memory limit exceeded"))
        except BaseException as exc:  # noqa: BLE001 - report everything to parent
            conn.send(("error", f"{type(exc).__name__}: {exc}"))
    except BaseException:  # result not picklable / pipe closed
        try:
            conn.send(("error", "result could not be serialized"))
        except BaseException:
            pass
    finally:
        conn.close()


def run_isolated(
    func: Callable[..., Any],
    args: Sequence[Any] = (),
    kwargs: Mapping[str, Any] | None = None,
    *,
    timeout: float = 30.0,
    memory_limit_mb: float | None = None,
    cpu_limit_seconds: float | None = None,
) -> Any:
    """Run ``func(*args, **kwargs)`` in a child process and return its result.

    Raises IsolatedExecutionTimeout (child terminated and reaped) on timeout and
    IsolatedExecutionError for exceptions, crashes, or unserializable results.
    ``memory_limit_mb`` (default None = unlimited) caps the child's address space;
    ``cpu_limit_seconds`` (default None) caps its CPU time via RLIMIT_CPU.
    The callable must be picklable when only the ``spawn`` start method exists.
    """
    for name, value in (("timeout", timeout), ("memory_limit_mb", memory_limit_mb),
                        ("cpu_limit_seconds", cpu_limit_seconds)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite number >= 0 (or None), got {value!r}")
    if memory_limit_mb or cpu_limit_seconds:
        try:
            import resource  # noqa: F401
        except ImportError:
            raise IsolatedExecutionError("memory/CPU limits are not supported on this platform") from None
    if "fork" not in mp.get_all_start_methods():
        raise IsolatedExecutionError(
            'isolated execution is not supported on this platform (no "fork" process start method)'
        )
    execution_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    outcome = "error"
    exitcode = None
    ctx = _context()
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child_conn, func, tuple(args), dict(kwargs or {}), memory_limit_mb, cpu_limit_seconds), daemon=True)
    try:
        proc.start()
        with _ACTIVE_LOCK:
            _ACTIVE[proc.pid] = proc
        child_conn.close()
        _emit_event("started", execution_id, started, pid=proc.pid)
        if not parent_conn.poll(timeout):
            outcome = "timeout"
            raise IsolatedExecutionTimeout(f"execution exceeded {timeout}s timeout")
        try:
            status, payload = parent_conn.recv()
        except EOFError:
            proc.join(1)
            exitcode = proc.exitcode
            outcome = "terminated"
            if cpu_limit_seconds and proc.exitcode in (-24, -9):  # SIGXCPU / SIGKILL backstop
                outcome = "cpu_limit_exceeded"
                raise IsolatedExecutionError("CPU limit exceeded") from None
            raise IsolatedExecutionError(f"child exited without result (exitcode={proc.exitcode})") from None
        if status == "ok":
            outcome = "completed"
            return payload
        outcome = "memory_limit_exceeded" if payload == "memory limit exceeded" else "error"
        raise IsolatedExecutionError(payload)
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.pop(proc.pid, None)
        _kill_process_group(proc.pid)
        if proc.is_alive():
            proc.terminate()
            proc.join(2)
            if proc.is_alive():
                proc.kill()
        if proc.pid is not None:
            proc.join()
            proc.close()
        child_conn.close()
        parent_conn.close()
        _emit_event("finished", execution_id, started, outcome=outcome, exitcode=exitcode)
