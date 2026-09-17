from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from .deployment_metrics import DeploymentMetricsCollector, capacity_metric_name

CAPACITY_STATUSES = ("OK", "WARNING", "CRITICAL")


class UnknownResourceError(KeyError):
    pass


@dataclass(frozen=True)
class ResourceDefinition:
    """The capacity and alerting thresholds for a monitored resource."""

    name: str
    capacity: float
    unit: str = "units"
    warning_threshold: float = 0.75
    critical_threshold: float = 0.9

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        if not 0 < self.warning_threshold < self.critical_threshold:
            raise ValueError(
                "thresholds must satisfy 0 < warning_threshold < critical_threshold"
            )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "capacity": self.capacity,
            "unit": self.unit,
            "warning_threshold": self.warning_threshold,
            "critical_threshold": self.critical_threshold,
        }


DEFAULT_RESOURCES: tuple[ResourceDefinition, ...] = (
    ResourceDefinition(name="cpu", capacity=100.0, unit="percent"),
    ResourceDefinition(
        name="memory", capacity=100.0, unit="percent", warning_threshold=0.8, critical_threshold=0.95
    ),
    ResourceDefinition(
        name="disk", capacity=100.0, unit="percent", warning_threshold=0.8, critical_threshold=0.95
    ),
    ResourceDefinition(name="network", capacity=1000.0, unit="mbps"),
    ResourceDefinition(
        name="worker_pool", capacity=50.0, unit="workers", warning_threshold=0.8, critical_threshold=0.95
    ),
)


@dataclass(frozen=True)
class ResourceMeasurement:
    """One immutable point-in-time utilization measurement."""

    resource: str
    used: float
    capacity: float
    utilization: float
    status: str
    collected_at: datetime

    def to_dict(self) -> dict:
        return {
            "resource": self.resource,
            "used": self.used,
            "capacity": self.capacity,
            "utilization": self.utilization,
            "status": self.status,
            "collected_at": self.collected_at.isoformat(),
        }


class DeploymentCapacityMonitor:
    """Tracks resource utilization for deployment infrastructure."""

    def __init__(self, resources: Optional[Iterable[ResourceDefinition]] = None) -> None:
        self._resources: dict[str, ResourceDefinition] = {}
        self._history: dict[str, list[ResourceMeasurement]] = {}
        self._lock = Lock()
        for resource in DEFAULT_RESOURCES if resources is None else resources:
            self.register_resource(resource)

    def register_resource(self, resource: ResourceDefinition) -> None:
        with self._lock:
            self._resources[resource.name] = resource
            self._history.setdefault(resource.name, [])

    def collect(
        self,
        name: str,
        used: float,
        *,
        timestamp: Optional[datetime] = None,
        metrics_collector: Optional[DeploymentMetricsCollector] = None,
    ) -> ResourceMeasurement:
        with self._lock:
            resource = self._resources.get(name)
        if resource is None:
            raise UnknownResourceError(name)
        if used < 0:
            raise ValueError("used must be non-negative")

        ratio = used / resource.capacity
        if ratio >= resource.critical_threshold:
            status = "CRITICAL"
        elif ratio >= resource.warning_threshold:
            status = "WARNING"
        else:
            status = "OK"

        measurement = ResourceMeasurement(
            resource=name,
            used=used,
            capacity=resource.capacity,
            utilization=ratio,
            status=status,
            collected_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._history[name].append(measurement)

        if metrics_collector is not None:
            metrics_collector.record(capacity_metric_name(name), ratio)

        return measurement

    def utilization(
        self, name: Optional[str] = None
    ) -> "ResourceMeasurement | dict[str, ResourceMeasurement] | None":
        with self._lock:
            if name is not None:
                history = self._history.get(name)
                if history is None:
                    raise UnknownResourceError(name)
                return history[-1] if history else None
            return {
                resource_name: measurements[-1]
                for resource_name, measurements in self._history.items()
                if measurements
            }

    def capacity(
        self, name: Optional[str] = None
    ) -> "ResourceDefinition | list[ResourceDefinition]":
        with self._lock:
            if name is not None:
                resource = self._resources.get(name)
                if resource is None:
                    raise UnknownResourceError(name)
                return resource
            return list(self._resources.values())


_monitor = DeploymentCapacityMonitor()


def get_deployment_capacity_monitor() -> DeploymentCapacityMonitor:
    return _monitor


router = APIRouter(prefix="/governance", tags=["governance-capacity"])


@router.get("/capacity")
def list_capacity() -> list[dict]:
    return [resource.to_dict() for resource in get_deployment_capacity_monitor().capacity()]


@router.get("/capacity/utilization")
def get_utilization(resource: Optional[str] = Query(default=None)):
    monitor = get_deployment_capacity_monitor()
    try:
        result = monitor.utilization(resource)
    except UnknownResourceError:
        raise HTTPException(status_code=404, detail="unknown resource")
    if resource is not None:
        return result.to_dict() if result is not None else None
    return {name: measurement.to_dict() for name, measurement in result.items()}


@router.post("/capacity/collect")
def collect_measurement(payload: dict = Body(...)) -> dict:
    from .deployment_metrics import get_deployment_metrics_collector

    resource = payload.get("resource")
    used = payload.get("used")
    if not resource or used is None:
        raise HTTPException(status_code=422, detail="resource and used are required")
    try:
        measurement = get_deployment_capacity_monitor().collect(
            resource, used, metrics_collector=get_deployment_metrics_collector()
        )
    except UnknownResourceError:
        raise HTTPException(status_code=404, detail="unknown resource")
    return measurement.to_dict()
