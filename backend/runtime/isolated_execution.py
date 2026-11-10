"""Process-isolated callable execution with enforceable timeouts."""
from __future__ import annotations

import multiprocessing as mp
from typing import Any, Callable, Mapping, Sequence


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


def _child(
    conn: Any,
    func: Callable[..., Any],
    args: Sequence[Any],
    kwargs: Mapping[str, Any],
    memory_limit_mb: float | None = None,
) -> None:
    try:
        try:
            if memory_limit_mb:
                _apply_memory_limit(memory_limit_mb)
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
) -> Any:
    """Run ``func(*args, **kwargs)`` in a child process and return its result.

    Raises IsolatedExecutionTimeout (child terminated and reaped) on timeout and
    IsolatedExecutionError for exceptions, crashes, or unserializable results.
    ``memory_limit_mb`` (default None = unlimited) caps the child's address space.
    The callable must be picklable when only the ``spawn`` start method exists.
    """
    if memory_limit_mb:
        try:
            import resource  # noqa: F401
        except ImportError:
            raise IsolatedExecutionError("memory limits are not supported on this platform") from None
    ctx = _context()
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child_conn, func, tuple(args), dict(kwargs or {}), memory_limit_mb), daemon=True)
    try:
        proc.start()
        child_conn.close()
        if not parent_conn.poll(timeout):
            raise IsolatedExecutionTimeout(f"execution exceeded {timeout}s timeout")
        try:
            status, payload = parent_conn.recv()
        except EOFError:
            proc.join(1)
            raise IsolatedExecutionError(f"child exited without result (exitcode={proc.exitcode})") from None
        if status == "ok":
            return payload
        raise IsolatedExecutionError(payload)
    finally:
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
