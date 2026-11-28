"""Bound how many notebook compiles the dashboard runs at once.

A compile imports and analyses notebook code and may build an image, so an unbounded burst of
POST /api/compile requests exhausts CPU, memory and disk. Capacity is NOTEBOOK_API_DASHBOARD_MAX_CONCURRENT_COMPILES
slots (default 4; 0 disables the limit). A slot is an exclusively `flock`ed file in
NOTEBOOK_API_DASHBOARD_COMPILE_SLOT_DIR (default: a per-user directory under the system temp dir), so the limit
holds across every worker process sharing that directory, and the kernel releases a slot when its
holder dies -- a crashed or killed compile can never leak one. A request that finds all slots busy
waits up to NOTEBOOK_API_DASHBOARD_COMPILE_QUEUE_SECONDS (default 10; 0 rejects at once) and then
gets 503 with Retry-After. CompileSlotMiddleware applies the limit to POST /api/compile; a request whose client disconnects while it
waits gives up at once (the stats count it as abandoned). On platforms without fcntl the limit is enforced per process only."""
from __future__ import annotations

import contextlib
import os
import tempfile
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

router = APIRouter()
_STATS = {"completed": 0, "rejected": 0, "failed": 0, "abandoned": 0}
_STATS_LOCK = threading.Lock()
_FALLBACKS: dict = {}  # without flock: one semaphore per configured limit, shared by this process only
_FALLBACK_LOCK = threading.Lock()


def _fallback(limit: int) -> threading.BoundedSemaphore:
    with _FALLBACK_LOCK:
        return _FALLBACKS.setdefault(limit, threading.BoundedSemaphore(limit))


def max_concurrent() -> int:
    raw = os.getenv("NOTEBOOK_API_DASHBOARD_MAX_CONCURRENT_COMPILES", "4").strip()
    return int(raw) if raw.isdigit() else 4


def queue_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("NOTEBOOK_API_DASHBOARD_COMPILE_QUEUE_SECONDS", "10")))
    except ValueError:
        return 10.0


def slot_dir() -> Path:
    configured = os.getenv("NOTEBOOK_API_DASHBOARD_COMPILE_SLOT_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / f"notebook2api-compile-slots-{os.getuid() if hasattr(os, 'getuid') else 0}"


def _try_lock(limit: int):
    """File descriptor of a slot this call now holds, or None when all `limit` slots are busy."""
    directory = slot_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(limit):
        fd = os.open(directory / f"slot-{index}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            os.close(fd)
    return None


def in_use(limit: int | None = None) -> int:
    limit = max_concurrent() if limit is None else limit
    if limit <= 0:
        return 0
    if fcntl is None:
        return limit - getattr(_fallback(limit), "_value", limit)
    held = 0
    for index in range(limit):
        path = slot_dir() / f"slot-{index}.lock"
        if not path.exists():
            continue
        fd = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            held += 1
        finally:
            os.close(fd)
    return held


@contextlib.contextmanager
def acquire():
    """Hold one compile slot for the block; raise HTTP 503 when none frees up in time."""
    limit = max_concurrent()
    if limit <= 0:
        yield
        return
    deadline = time.monotonic() + queue_seconds()
    semaphore = _fallback(limit) if fcntl is None else None
    while True:
        if semaphore is None:
            fd = _try_lock(limit)
        else:
            fd = object() if semaphore.acquire(blocking=False) else None
        if fd is not None:
            break
        if time.monotonic() >= deadline:
            with _STATS_LOCK:
                _STATS["rejected"] += 1
            retry = max(1, int(queue_seconds()) or 5)
            raise HTTPException(503, f"All {limit} compile slots are busy; try again shortly",
                                headers={"Retry-After": str(retry)})
        time.sleep(0.05)
    try:
        yield
    except BaseException:
        with _STATS_LOCK:
            _STATS["failed"] += 1
        raise
    else:
        with _STATS_LOCK:
            _STATS["completed"] += 1
    finally:
        if semaphore is None:
            os.close(fd)  # closing the descriptor releases the flock
        else:
            semaphore.release()


def stats() -> dict:
    limit = max_concurrent()
    with _STATS_LOCK:
        return {"limit": limit, "in_use": in_use(limit), "queue_seconds": queue_seconds(), **_STATS}


@router.get("/api/compile/capacity")
def compile_capacity():
    """Compile slots: the limit, how many are in use now (all workers), and this worker's totals."""
    return stats()


class CompileSlotMiddleware:
    """Pure ASGI middleware that admits at most `max_concurrent()` POST /api/compile requests at a time.

    It sits inside the dashboard's authentication and rate-limit middleware (so only authorized requests
    queue). It buffers the request body, then polls for a slot while listening for the client's
    disconnect, which a plain endpoint behind BaseHTTPMiddleware cannot see: a client that gave up leaves
    the queue at once, and no worker thread is held while a request waits. The slot is held until the
    response has been sent."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limit = max_concurrent()
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/compile" or limit <= 0:
            return await self.app(scope, receive, send)
        import asyncio

        from starlette.responses import JSONResponse

        chunks = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return self._abandoned()
            chunks.append(message)
            if not message.get("more_body"):
                break
        semaphore = _fallback(limit) if fcntl is None else None
        deadline = time.monotonic() + queue_seconds()
        while True:
            handle = _try_lock(limit) if semaphore is None else (object() if semaphore.acquire(blocking=False) else None)
            if handle is not None:
                break
            if time.monotonic() >= deadline:
                with _STATS_LOCK:
                    _STATS["rejected"] += 1
                retry = max(1, int(queue_seconds()) or 5)
                return await JSONResponse({"detail": f"All {limit} compile slots are busy; try again shortly"}, status_code=503,
                                          headers={"Retry-After": str(retry)})(scope, receive, send)
            try:
                message = await asyncio.wait_for(receive(), 0.05)
            except asyncio.TimeoutError:
                continue
            if message["type"] == "http.disconnect":
                return self._abandoned()
        pending = list(chunks)
        status = {"code": 200}

        async def replay():
            return pending.pop(0) if pending else await receive()

        async def watch(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, replay, watch)
            outcome = "failed" if status["code"] >= 400 else "completed"
        except BaseException:
            outcome = "failed"
            raise
        finally:
            if semaphore is None:
                os.close(handle)
            else:
                semaphore.release()
        with _STATS_LOCK:
            _STATS[outcome] += 1

    @staticmethod
    def _abandoned():
        with _STATS_LOCK:
            _STATS["abandoned"] += 1
