from dataclasses import dataclass
from typing import Callable, List, Optional

from backend.observability.alert_engine import AlertRuleEngine
from backend.observability.anomaly_detection import AnomalyDetectionEngine
from backend.observability.dashboard import ObservabilityDashboardAPI
from backend.observability.distributed_tracing import DistributedTracingEngine
from backend.observability.export_service import TelemetryExportService
from backend.observability.health_checks import HealthCheck, HealthCheckFramework
from backend.observability.log_aggregation import LogAggregationService
from backend.observability.metrics_registry import MetricsRegistry
from backend.observability.metrics_storage import MetricsStorageEngine
from backend.observability.observability_analytics import ObservabilityAnalyticsService
from backend.observability.service_discovery import ServiceDiscoveryMonitor, ServiceNode
from backend.observability.telemetry_collector import TelemetryCollector


VALID_STATES = ("stopped", "initializing", "running", "shutdown")


@dataclass
class ObservabilityRuntime:
    metrics_registry: MetricsRegistry
    telemetry_collector: TelemetryCollector
    tracing_engine: DistributedTracingEngine
    log_service: LogAggregationService
    alert_engine: AlertRuleEngine
    health_framework: HealthCheckFramework
    discovery_monitor: Optional[ServiceDiscoveryMonitor]
    storage_engine: MetricsStorageEngine
    anomaly_engine: AnomalyDetectionEngine
    analytics_service: ObservabilityAnalyticsService
    dashboard: ObservabilityDashboardAPI
    export_service: TelemetryExportService


class ObservabilityBootstrap:
    def __init__(self, discovery_scan_fn: Optional[Callable[[], List[ServiceNode]]] = None):
        self._discovery_scan_fn = discovery_scan_fn
        self._runtime: Optional[ObservabilityRuntime] = None
        self._state = "stopped"

    @property
    def state(self) -> str:
        return self._state

    def register_services(self) -> ObservabilityRuntime:
        storage_engine = MetricsStorageEngine()
        tracing_engine = DistributedTracingEngine()
        log_service = LogAggregationService()
        analytics_service = ObservabilityAnalyticsService(storage_engine=storage_engine)
        export_service = TelemetryExportService(storage_engine, tracing_engine, log_service)
        alert_engine = AlertRuleEngine()
        health_framework = HealthCheckFramework()
        dashboard = ObservabilityDashboardAPI(
            analytics_service,
            alert_engine,
            health_framework,
            export_service=export_service,
        )

        self._runtime = ObservabilityRuntime(
            metrics_registry=MetricsRegistry(),
            telemetry_collector=TelemetryCollector(),
            tracing_engine=tracing_engine,
            log_service=log_service,
            alert_engine=alert_engine,
            health_framework=health_framework,
            discovery_monitor=(
                ServiceDiscoveryMonitor(self._discovery_scan_fn)
                if self._discovery_scan_fn is not None
                else None
            ),
            storage_engine=storage_engine,
            anomaly_engine=AnomalyDetectionEngine(),
            analytics_service=analytics_service,
            dashboard=dashboard,
            export_service=export_service,
        )
        return self._runtime

    def wire_components(self) -> ObservabilityRuntime:
        if self._runtime is None:
            raise ValueError("register_services() must be called before wire_components()")

        runtime = self._runtime
        runtime.health_framework.register(
            HealthCheck(
                name="telemetry_pipeline",
                check_type="liveness",
                check_fn=lambda: True,
            )
        )

        if runtime.discovery_monitor is not None:
            runtime.discovery_monitor.refresh()
            runtime.health_framework.register_from_topology(
                runtime.discovery_monitor.topology().nodes,
                check_fn=lambda node: True,
            )

        return runtime

    def initialize(self) -> ObservabilityRuntime:
        if self._state == "running":
            raise ValueError("Observability subsystem is already running")

        self._state = "initializing"
        self.register_services()
        self.wire_components()
        self._state = "running"
        return self._runtime

    def shutdown(self) -> None:
        if self._state != "running":
            raise ValueError("Observability subsystem is not running")
        self._state = "shutdown"
