from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .worker_discovery import WorkerDiscoveryService, get_worker_discovery_service
from .worker_registry import WorkerRegistry, get_worker_registry

HEALTHY = "healthy"
DEGRADED = "degraded"
UNHEALTHY = "unhealthy"

# (warn, fail) thresholds per probe; cpu/memory/disk are percent-used, network is latency in ms.
_THRESHOLDS = {
    "cpu": (80.0, 95.0),
    "memory": (80.0, 95.0),
    "disk": (85.0, 95.0),
    "network": (200.0, 1000.0),
}


def _grade(value: float, warn: float, fail: float) -> str:
    if value >= fail:
        return "fail"
    if value >= warn:
        return "warn"
    return "ok"


@dataclass(frozen=True)
class HealthStatus:
    """The outcome of a single health probe (cpu, memory, disk, network, or heartbeat)."""

    check: str
    passed: bool
    value: Optional[float] = None
    detail: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "passed": self.passed,
            "value": self.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HealthReport:
    """The aggregate health of a worker at a point in time."""

    worker_id: str
    status: str
    checks: tuple
    checked_at: datetime

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
            "checked_at": self.checked_at.isoformat(),
        }


class WorkerHealthManager:
    """Runs CPU/memory/disk/network/heartbeat probes and isolates workers that fail them."""

    def __init__(
        self,
        registry: WorkerRegistry,
        discovery: WorkerDiscoveryService,
        *,
        heartbeat_timeout_seconds: float = 30.0,
    ) -> None:
        self._registry = registry
        self._discovery = discovery
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self._reports: dict = {}
        self._lock = Lock()

    def check(self, worker_id: str, *, metrics: Optional[dict] = None) -> HealthReport:
        node = self._registry.get(worker_id)
        if node is None:
            raise KeyError(worker_id)

        metrics = metrics or {}
        elapsed = (datetime.now(timezone.utc) - node.last_seen_at).total_seconds()
        heartbeat_ok = elapsed <= self._heartbeat_timeout_seconds

        checks = (
            self._probe("cpu", metrics.get("cpu_percent", 0.0)),
            self._probe("memory", metrics.get("memory_percent", 0.0)),
            self._probe("disk", metrics.get("disk_percent", 0.0)),
            self._probe("network", metrics.get("network_latency_ms", 0.0)),
            HealthStatus(
                check="heartbeat",
                passed=heartbeat_ok,
                value=elapsed,
                detail=None if heartbeat_ok else f"no heartbeat for {elapsed:.1f}s",
            ),
        )
        status = self._aggregate(checks)
        report = HealthReport(worker_id=worker_id, status=status, checks=checks, checked_at=datetime.now(timezone.utc))

        with self._lock:
            self._reports[worker_id] = report
        self._discovery.set_health(worker_id, status)
        return report

    def _probe(self, name: str, value: float) -> HealthStatus:
        warn, fail = _THRESHOLDS[name]
        grade = _grade(value, warn, fail)
        return HealthStatus(
            check=name,
            passed=grade != "fail",
            value=value,
            detail=None if grade == "ok" else f"{name} at {value} ({grade})",
        )

    def _aggregate(self, checks: tuple) -> str:
        if any(not check.passed for check in checks):
            return UNHEALTHY
        if any(check.detail is not None for check in checks):
            return DEGRADED
        return HEALTHY

    def heartbeat(self, worker_id: str, *, metrics: Optional[dict] = None) -> HealthReport:
        self._discovery.heartbeat(worker_id)
        return self.check(worker_id, metrics=metrics)

    def mark_unhealthy(self, worker_id: str, *, reason: Optional[str] = None) -> HealthReport:
        node = self._registry.get(worker_id)
        if node is None:
            raise KeyError(worker_id)

        check = HealthStatus(check="manual", passed=False, value=None, detail=reason or "marked unhealthy")
        report = HealthReport(worker_id=worker_id, status=UNHEALTHY, checks=(check,), checked_at=datetime.now(timezone.utc))
        with self._lock:
            self._reports[worker_id] = report
        self._discovery.set_health(worker_id, UNHEALTHY)
        return report

    def recover(self, worker_id: str, *, metrics: Optional[dict] = None) -> HealthReport:
        if self._registry.get(worker_id) is None:
            raise KeyError(worker_id)

        previous = self._reports.get(worker_id)
        if previous is None or previous.status != UNHEALTHY:
            raise ValueError(f"worker '{worker_id}' is not currently marked unhealthy")
        return self.check(worker_id, metrics=metrics)

    def get_report(self, worker_id: str) -> Optional[HealthReport]:
        return self._reports.get(worker_id)

    def list_reports(self, *, status: Optional[str] = None) -> list:
        with self._lock:
            reports = list(self._reports.values())
        if status is not None:
            reports = [report for report in reports if report.status == status]
        return sorted(reports, key=lambda report: report.worker_id)


_worker_health_manager = WorkerHealthManager(get_worker_registry(), get_worker_discovery_service())


def get_worker_health_manager() -> WorkerHealthManager:
    return _worker_health_manager


router = APIRouter(prefix="/cluster/health", tags=["worker-health"])


@router.get("")
def list_health_endpoint(
    status: Optional[str] = None,
    manager: WorkerHealthManager = Depends(get_worker_health_manager),
) -> list:
    return [report.to_dict() for report in manager.list_reports(status=status)]


@router.get("/{worker_id}")
def get_health_endpoint(
    worker_id: str,
    manager: WorkerHealthManager = Depends(get_worker_health_manager),
) -> dict:
    report = manager.get_report(worker_id)
    if report is None:
        try:
            report = manager.check(worker_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"worker '{worker_id}' not found")
    return report.to_dict()


@router.post("/{worker_id}/heartbeat")
def heartbeat_endpoint(
    worker_id: str,
    payload: dict,
    manager: WorkerHealthManager = Depends(get_worker_health_manager),
) -> dict:
    try:
        report = manager.heartbeat(worker_id, metrics=payload.get("metrics"))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"worker '{worker_id}' not found")
    return report.to_dict()


@router.post("/{worker_id}/recover")
def recover_endpoint(
    worker_id: str,
    payload: dict,
    manager: WorkerHealthManager = Depends(get_worker_health_manager),
) -> dict:
    try:
        report = manager.recover(worker_id, metrics=payload.get("metrics"))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"worker '{worker_id}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return report.to_dict()
