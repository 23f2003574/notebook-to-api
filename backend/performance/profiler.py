from __future__ import annotations

import resource
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .resource_pool import (
    PoolAlreadyExistsError,
    PoolType,
    ResourcePoolManager,
    UnknownPoolError,
    get_resource_pool_manager,
)


class UnknownSessionError(KeyError):
    pass


class SessionNotRunningError(ValueError):
    pass


class SessionAlreadyExistsError(ValueError):
    pass


@dataclass
class ProfileSession:
    """A single profiling run, from start() through stop()."""

    session_id: str
    name: str
    status: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    execution_time_ms: Optional[float] = None
    cpu_usage_percent: Optional[float] = None
    memory_usage_bytes: Optional[int] = None
    io_time_ms: Optional[float] = None
    timeline: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "execution_time_ms": self.execution_time_ms,
            "cpu_usage_percent": self.cpu_usage_percent,
            "memory_usage_bytes": self.memory_usage_bytes,
            "io_time_ms": self.io_time_ms,
            "timeline": list(self.timeline),
        }


@dataclass(frozen=True)
class PerformanceReport:
    """A summary of a profiling session's resource usage."""

    session_id: str
    name: str
    status: str
    execution_time_ms: Optional[float]
    cpu_usage_percent: Optional[float]
    memory_usage_bytes: Optional[int]
    io_time_ms: Optional[float]
    checkpoint_count: int
    timeline: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "status": self.status,
            "execution_time_ms": self.execution_time_ms,
            "cpu_usage_percent": self.cpu_usage_percent,
            "memory_usage_bytes": self.memory_usage_bytes,
            "io_time_ms": self.io_time_ms,
            "checkpoint_count": self.checkpoint_count,
            "timeline": list(self.timeline),
        }


class PerformanceProfiler:
    """Measures execution time, CPU/memory usage, and I/O time for named operations."""

    def __init__(self) -> None:
        self._sessions: dict = {}
        self._clocks: dict = {}
        self._lock = Lock()
        self._sequence = 0

    def start(self, name: str, *, session_id: Optional[str] = None) -> ProfileSession:
        if not name:
            raise ValueError("name is required")
        with self._lock:
            self._sequence += 1
            sid = session_id or f"session-{self._sequence}"
            if sid in self._sessions:
                raise SessionAlreadyExistsError(sid)
            session = ProfileSession(
                session_id=sid,
                name=name,
                status="running",
                started_at=datetime.now(timezone.utc),
            )
            self._sessions[sid] = session
            self._clocks[sid] = (perf_counter(), resource.getrusage(resource.RUSAGE_SELF))
            return session

    def profile(self, session_id: str, label: str) -> dict:
        if not label:
            raise ValueError("label is required")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise UnknownSessionError(session_id)
            if session.status != "running":
                raise SessionNotRunningError(session_id)
            start_perf, _ = self._clocks[session_id]
            checkpoint = {
                "label": label,
                "elapsed_ms": (perf_counter() - start_perf) * 1000,
            }
            session.timeline.append(checkpoint)
            return checkpoint

    def stop(
        self,
        session_id: str,
        *,
        cpu_usage_percent: Optional[float] = None,
        memory_usage_bytes: Optional[int] = None,
        io_time_ms: Optional[float] = None,
    ) -> ProfileSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise UnknownSessionError(session_id)
            if session.status != "running":
                raise SessionNotRunningError(session_id)

            start_perf, start_rusage = self._clocks[session_id]
            end_perf = perf_counter()
            end_rusage = resource.getrusage(resource.RUSAGE_SELF)

            execution_time_ms = (end_perf - start_perf) * 1000
            measured_cpu_time_ms = (
                (end_rusage.ru_utime + end_rusage.ru_stime)
                - (start_rusage.ru_utime + start_rusage.ru_stime)
            ) * 1000
            measured_cpu_percent = (
                min(100.0, (measured_cpu_time_ms / execution_time_ms) * 100)
                if execution_time_ms > 0
                else 0.0
            )
            measured_memory_bytes = end_rusage.ru_maxrss * 1024
            measured_io_time_ms = max(0.0, execution_time_ms - measured_cpu_time_ms)

            session.ended_at = datetime.now(timezone.utc)
            session.status = "stopped"
            session.execution_time_ms = execution_time_ms
            session.cpu_usage_percent = (
                cpu_usage_percent if cpu_usage_percent is not None else measured_cpu_percent
            )
            session.memory_usage_bytes = (
                memory_usage_bytes if memory_usage_bytes is not None else measured_memory_bytes
            )
            session.io_time_ms = io_time_ms if io_time_ms is not None else measured_io_time_ms
            return session

    def report(self, session_id: str) -> PerformanceReport:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise UnknownSessionError(session_id)
            return PerformanceReport(
                session_id=session.session_id,
                name=session.name,
                status=session.status,
                execution_time_ms=session.execution_time_ms,
                cpu_usage_percent=session.cpu_usage_percent,
                memory_usage_bytes=session.memory_usage_bytes,
                io_time_ms=session.io_time_ms,
                checkpoint_count=len(session.timeline),
                timeline=list(session.timeline),
            )


_performance_profiler = PerformanceProfiler()


def get_performance_profiler() -> PerformanceProfiler:
    return _performance_profiler


pool_router = APIRouter(prefix="/performance/pools", tags=["resource-pool"])


@pool_router.post("", status_code=201)
def create_pool_endpoint(
    payload: dict = Body(default={}),
    manager: ResourcePoolManager = Depends(get_resource_pool_manager),
) -> dict:
    try:
        pool_type = PoolType(payload.get("pool_type", ""))
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown pool type")
    try:
        pool = manager.create_pool(
            payload.get("name", ""),
            pool_type=pool_type,
            min_size=payload.get("min_size", 0),
            max_size=payload.get("max_size", 1),
            idle_timeout_seconds=payload.get("idle_timeout_seconds"),
        )
    except PoolAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return pool.to_dict()


@pool_router.get("")
def list_pools_endpoint(
    manager: ResourcePoolManager = Depends(get_resource_pool_manager),
) -> dict:
    return {"pools": [pool.to_dict() for pool in manager.list_pools()]}


@pool_router.get("/{pool}/stats")
def pool_stats_endpoint(
    pool: str,
    manager: ResourcePoolManager = Depends(get_resource_pool_manager),
) -> dict:
    try:
        return manager.stats(pool)
    except UnknownPoolError:
        raise HTTPException(status_code=404, detail="unknown pool")
