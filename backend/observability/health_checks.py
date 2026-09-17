from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from backend.observability.service_discovery import ServiceNode


VALID_CHECK_TYPES = ("liveness", "readiness", "startup", "dependency")
VALID_STATUSES = ("healthy", "unhealthy", "unknown")


@dataclass
class HealthCheck:
    name: str
    check_type: str
    check_fn: Callable[[], bool]
    timeout_seconds: float = 5.0
    depends_on: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.check_type not in VALID_CHECK_TYPES:
            raise ValueError(
                f"Unsupported check_type '{self.check_type}'. Expected one of {VALID_CHECK_TYPES}."
            )


@dataclass
class HealthReport:
    name: str
    status: str
    checked_at: str
    error: Optional[str] = None


class HealthCheckFramework:
    def __init__(self):
        self._checks: Dict[str, HealthCheck] = {}
        self._latest: Dict[str, HealthReport] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)

    def register(self, check: HealthCheck) -> HealthCheck:
        if check.name in self._checks:
            raise ValueError(f"Health check '{check.name}' is already registered")

        for dependency in check.depends_on:
            if dependency not in self._checks:
                raise KeyError(f"Dependency '{dependency}' is not registered")

        self._checks[check.name] = check
        return check

    def run(self, name: str) -> HealthReport:
        check = self._checks.get(name)
        if check is None:
            raise KeyError(f"Health check '{name}' is not registered")

        for dependency in check.depends_on:
            dependency_report = self.run(dependency)
            if dependency_report.status != "healthy":
                report = HealthReport(
                    name=name,
                    status="unhealthy",
                    checked_at=_utc_now_iso(),
                    error=f"dependency '{dependency}' is unhealthy",
                )
                self._latest[name] = report
                return report

        future = self._executor.submit(check.check_fn)
        try:
            healthy = future.result(timeout=check.timeout_seconds)
            status = "healthy" if healthy else "unhealthy"
            error = None if healthy else "check returned an unhealthy result"
        except FutureTimeoutError:
            status = "unhealthy"
            error = f"check timed out after {check.timeout_seconds}s"
        except Exception as exc:
            status = "unhealthy"
            error = str(exc)

        report = HealthReport(name=name, status=status, checked_at=_utc_now_iso(), error=error)
        self._latest[name] = report
        return report

    def aggregate(self) -> HealthReport:
        reports = [self.run(name) for name in self._checks]
        overall_status = (
            "healthy" if all(report.status == "healthy" for report in reports) else "unhealthy"
        )
        return HealthReport(name="system", status=overall_status, checked_at=_utc_now_iso())

    def status(self, name: str) -> HealthReport:
        report = self._latest.get(name)
        if report is None:
            raise KeyError(f"No health report available for '{name}'")
        return report

    def is_registered(self, name: str) -> bool:
        return name in self._checks

    def register_from_topology(
        self,
        nodes: List[ServiceNode],
        check_fn: Callable[[ServiceNode], bool],
    ) -> List[HealthCheck]:
        registered = []
        for node in nodes:
            if self.is_registered(node.name):
                continue

            depends_on = [dep for dep in node.depends_on if self.is_registered(dep)]
            check = self.register(
                HealthCheck(
                    name=node.name,
                    check_type="dependency",
                    check_fn=lambda node=node: check_fn(node),
                    depends_on=depends_on,
                )
            )
            registered.append(check)
        return registered


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
