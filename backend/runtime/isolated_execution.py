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


def _child(conn: Any, func: Callable[..., Any], args: Sequence[Any], kwargs: Mapping[str, Any]) -> None:
    try:
        try:
            conn.send(("ok", func(*args, **kwargs)))
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
) -> Any:
    """Run ``func(*args, **kwargs)`` in a child process and return its result.

    Raises IsolatedExecutionTimeout (child terminated and reaped) on timeout and
    IsolatedExecutionError for exceptions, crashes, or unserializable results.
    The callable must be picklable when only the ``spawn`` start method exists.
    """
    ctx = _context()
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child_conn, func, tuple(args), dict(kwargs or {})), daemon=True)
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
        parent_conn.close()
