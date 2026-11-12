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


class IsolatedExecutionCancelled(IsolatedExecutionError):
    """The caller's ``cancel_event`` was set; the child was terminated and reaped."""


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

# fork() copies only the calling thread, so a lock another thread of the server
# holds at that instant stays locked forever in the child. Forks are serialized
# (no two threads fork at once), and a child must prove it came up (the "started"
# message) within this many seconds or the call fails explicitly instead of hanging.
_FORK_LOCK = threading.Lock()
_START_TIMEOUT_SECONDS = 10.0


_WORKERS: dict[int, Any] = {}


def terminate_active_processes(grace_seconds: float = 2.0) -> int:
    """Stop every isolated child still running; returns how many were signalled.

    For application shutdown. Each ``run_isolated`` call owns reaping its own
    child (two threads reaping one process race), so this only signals: SIGTERM
    to each child's process group, wait up to ``grace_seconds`` for the owners
    to finish and deregister, then SIGKILL what is left. Their calls report a
    controlled failure and free their capacity slot. Idle workers have no
    owner thread, so they are shut down here.
    """
    with _ACTIVE_LOCK:
        procs = dict(_ACTIVE)
        workers = list(_WORKERS.values())
    total = len(procs)
    for worker in workers:
        if worker._lock.acquire(blocking=False):  # idle: nobody else will reap it
            try:
                worker._discard()
            finally:
                worker._lock.release()
        elif worker._proc is not None:
            procs[worker._proc.pid] = worker._proc
            total += 1

    def _signal(sig: int) -> None:
        for pid, proc in procs.items():
            try:
                if hasattr(os, "killpg"):
                    os.killpg(pid, sig)
                else:
                    os.kill(pid, sig)
            except (OSError, ValueError):
                pass

    def _remaining() -> list[int]:
        with _ACTIVE_LOCK:
            return [pid for pid in procs if pid in _ACTIVE or pid in _WORKERS]

    _signal(signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while _remaining() and time.monotonic() < deadline:
        time.sleep(0.02)
    if _remaining():
        _signal(signal.SIGKILL)
        deadline = time.monotonic() + 2.0
        while _remaining() and time.monotonic() < deadline:
            time.sleep(0.02)
    return total


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
            conn.send(("started", None))
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
    cancel_event: Any = None,
    on_start: Callable[[int], None] | None = None,
) -> Any:
    """Run ``func(*args, **kwargs)`` in a child process and return its result.

    ``cancel_event`` (a ``threading.Event``) aborts the call when set: the child's
    process group is killed and reaped here and IsolatedExecutionCancelled is
    raised. ``on_start(pid)`` is called once the child is running.

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
        with _FORK_LOCK:
            proc.start()
        with _ACTIVE_LOCK:
            _ACTIVE[proc.pid] = proc
        child_conn.close()
        _emit_event("started", execution_id, started, pid=proc.pid)
        if on_start is not None:
            on_start(proc.pid)
        deadline = None if timeout is None else started + timeout
        start_deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        acked = False
        early = None
        while True:
            if cancel_event is not None and cancel_event.is_set():
                outcome = "cancelled"
                raise IsolatedExecutionCancelled("execution cancelled")
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                if acked and parent_conn.poll(0):
                    break
                outcome = "timeout"
                raise IsolatedExecutionTimeout(f"execution exceeded {timeout}s timeout")
            wait = 0.05 if cancel_event is not None else None
            if not acked:
                until_start = start_deadline - time.monotonic()
                if until_start <= 0:
                    outcome = "start_failed"
                    raise IsolatedExecutionError(
                        f"isolated child did not start within {_START_TIMEOUT_SECONDS:g}s "
                        "(it was killed; the server may have forked while holding a lock)"
                    )
                wait = until_start if wait is None else min(wait, until_start)
            if remaining is not None:
                wait = remaining if wait is None else min(wait, remaining)
            if parent_conn.poll(wait):
                if acked:
                    break
                try:
                    early = parent_conn.recv()
                except EOFError:
                    break  # died before the handshake: handled as "exited without result" below
                if early[0] == "started":
                    acked = True
                    early = None
                    continue
                break  # an error reported before the handshake (e.g. limits could not be applied)
        try:
            status, payload = early if early is not None else parent_conn.recv()
        except EOFError:
            with _FORK_LOCK:
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
        pid = proc.pid
        _kill_process_group(pid)
        try:
            # multiprocessing's Process.start() reaps *every* finished child
            # (_cleanup); doing that while another thread join()s/close()s its
            # own child makes waitpid fail with ECHILD ("Cannot close a process
            # while it is still running"). Reaping shares the fork lock.
            with _FORK_LOCK:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(2)
                    if proc.is_alive():
                        proc.kill()
                if proc.pid is not None:
                    proc.join()
                    proc.close()
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.pop(pid, None)
        child_conn.close()
        parent_conn.close()
        _emit_event("finished", execution_id, started, outcome=outcome, exitcode=exitcode)


def _worker_main(conn: Any, handler: Callable[[Any], Any], memory_limit_mb: float | None,
                 cpu_limit_seconds: float | None) -> None:
    global _ACTIVE_LOCK, _FORK_LOCK
    # Daemonic (so the app's exit reaps us) yet allowed its own children.
    mp.current_process()._config["daemon"] = False
    if hasattr(os, "setsid"):
        try:
            os.setsid()
        except OSError:
            pass
    # Forked from the app: forget its children and any lock held at fork time.
    _ACTIVE_LOCK = threading.Lock()
    _FORK_LOCK = threading.Lock()
    _ACTIVE.clear()
    _WORKERS.clear()

    def _stop(*_: Any) -> None:
        terminate_active_processes(0.5)
        os._exit(0)

    signal.signal(signal.SIGTERM, _stop)
    while True:
        try:
            message = conn.recv()
        except EOFError:
            return
        if message is None:
            return
        payload, timeout = message
        try:
            # Every request runs in a fresh child forked from this idle worker,
            # so nothing a request does to its state survives it.
            reply = ("ok", run_isolated(handler, args=(payload,), timeout=timeout,
                                        memory_limit_mb=memory_limit_mb, cpu_limit_seconds=cpu_limit_seconds))
        except IsolatedExecutionTimeout as exc:
            reply = ("timeout", str(exc))
        except IsolatedExecutionError as exc:
            reply = ("error", str(exc))
        try:
            conn.send(reply)
        except Exception:  # noqa: BLE001 - unpicklable result
            conn.send(("error", "result could not be serialized"))


class IsolatedWorker:
    """A warm helper process that serves many requests (opt-in; not used by default).

    ``handler`` is fixed when the worker starts (it is inherited by fork, never
    pickled); requests are picklable payloads. Each request runs through
    ``run_isolated`` in a fresh child forked from the idle worker, so state
    never carries over between requests and the memory/CPU/timeout semantics
    are identical to the per-call path. The worker keeps only the startup cost
    (a small single-threaded process instead of forking the whole app). Any
    worker failure (crash, timeout, protocol error) kills it, and the next
    request starts a fresh one. One request at a time per worker.
    """

    def __init__(self, handler: Callable[[Any], Any], *, memory_limit_mb: float | None = None,
                 cpu_limit_seconds: float | None = None, startup_grace_seconds: float = 2.0) -> None:
        self._handler = handler
        self._memory_limit_mb = memory_limit_mb
        self._cpu_limit_seconds = cpu_limit_seconds
        self._grace = startup_grace_seconds
        self._lock = threading.Lock()
        self._proc: Any = None
        self._conn: Any = None
        self.starts = 0

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def _start(self) -> None:
        if "fork" not in mp.get_all_start_methods():
            raise IsolatedExecutionError('isolated workers are not supported on this platform (no "fork" start method)')
        ctx = _context()
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        proc = ctx.Process(target=_worker_main, daemon=True,
                           args=(child_conn, self._handler, self._memory_limit_mb, self._cpu_limit_seconds))
        proc.start()
        child_conn.close()
        self._proc, self._conn = proc, parent_conn
        self.starts += 1
        with _ACTIVE_LOCK:
            _WORKERS[proc.pid] = self

    def _discard(self) -> None:
        proc, conn, self._proc, self._conn = self._proc, self._conn, None, None
        if proc is None:
            return
        with _ACTIVE_LOCK:
            _WORKERS.pop(proc.pid, None)
        try:
            if proc.is_alive():
                proc.terminate()
                proc.join(self._grace)
                if proc.is_alive():
                    proc.kill()
            _kill_process_group(proc.pid)
            proc.join()
            proc.close()
        except (OSError, ValueError):
            pass
        try:
            conn.close()
        except OSError:
            pass

    def request(self, payload: Any, timeout: float | None = None) -> Any:
        with self._lock:
            if not self.running:
                self._discard()
                self._start()
            try:
                self._conn.send((payload, timeout))
                wait = None if not timeout else timeout + self._grace + 1.0
                if not self._conn.poll(wait):
                    raise IsolatedExecutionTimeout(f"worker did not answer within {timeout}s")
                status, value = self._conn.recv()
            except IsolatedExecutionError:
                self._discard()
                raise
            except (EOFError, OSError):
                self._discard()
                raise IsolatedExecutionError("worker exited without a result") from None
            if status == "ok":
                return value
            if status == "timeout":
                raise IsolatedExecutionTimeout(value)
            raise IsolatedExecutionError(value)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None and self.running:
                try:
                    self._conn.send(None)
                    self._proc.join(self._grace)
                except (OSError, ValueError):
                    pass
            self._discard()
