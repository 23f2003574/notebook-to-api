from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .deployment_governance_metrics import (
    GovernanceIntegrityMetrics,
    GovernanceIntegrityMetricsService,
)
from .deployment_governance_metrics_alerts import (
    GovernanceIntegrityMetricAlert,
    GovernanceIntegrityMetricsAlertService,
)
from .deployment_governance_metrics_dashboard import (
    GovernanceIntegrityMetricsDashboard,
    GovernanceIntegrityMetricsDashboardService,
)
from .deployment_governance_metrics_collector import (
    GovernanceIntegrityMetricsCollector,
)
from .deployment_governance_metrics_retention import (
    GovernanceIntegrityMetricsRetentionService,
)
from .deployment_governance_metrics_config import (
    GovernanceIntegrityMetricsConfig,
    GovernanceIntegrityMetricsConfigService,
)
from .deployment_governance_metrics_bootstrap import (
    GovernanceIntegrityMetricsBootstrap,
)
from .deployment_governance_logging import (
    GovernanceIntegrityLogger,
)
from .deployment_governance_log_repository import (
    GovernanceLogRepository,
)
from .deployment_governance_log_rotation import (
    GovernanceLogRotationService,
)


class GovernanceIntegrityRuntimeState(
    str,
    Enum,
):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


@dataclass(frozen=True)
class GovernanceIntegrityRuntimeStatus:
    state: GovernanceIntegrityRuntimeState
    started_at: Optional[datetime]
    uptime_seconds: int
    worker_iterations: int
    active_dispatches: int

    def __post_init__(self):
        if self.started_at is not None and self.started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        if self.uptime_seconds < 0:
            raise ValueError("uptime_seconds must be >= 0")
        if self.worker_iterations < 0:
            raise ValueError("worker_iterations must be >= 0")
        if self.active_dispatches < 0:
            raise ValueError("active_dispatches must be >= 0")


class GovernanceIntegrityDeliveryRuntime:

    def __init__(
        self,
        worker,
        scheduler,
        provider_registry,
        clock,
        metrics_service: Optional[GovernanceIntegrityMetricsService] = None,
        alert_service: Optional[GovernanceIntegrityMetricsAlertService] = None,
        metrics_collector: Optional[GovernanceIntegrityMetricsCollector] = None,
        metrics_retention_service: Optional[GovernanceIntegrityMetricsRetentionService] = None,
        config_service: Optional[GovernanceIntegrityMetricsConfigService] = None,
        metrics_bootstrap: Optional[GovernanceIntegrityMetricsBootstrap] = None,
        logger: Optional[GovernanceIntegrityLogger] = None,
        log_repository: Optional[GovernanceLogRepository] = None,
        log_rotation_service: Optional[GovernanceLogRotationService] = None
    ):
        self.worker = worker
        self.scheduler = scheduler
        self.provider_registry = provider_registry
        self.clock = clock
        self.logger = logger
        self.log_repository = log_repository
        self.log_rotation_service = log_rotation_service

        # A given metrics_bootstrap replaces the previous pattern of
        # wiring each metrics-related dependency independently: any
        # of the five below that were not explicitly passed are
        # filled in from it, but an explicit value always wins.
        if metrics_bootstrap is not None:
            metrics_service = (
                metrics_service
                if metrics_service is not None
                else metrics_bootstrap.metrics_service
            )
            alert_service = (
                alert_service
                if alert_service is not None
                else metrics_bootstrap.alert_service
            )
            metrics_collector = (
                metrics_collector
                if metrics_collector is not None
                else metrics_bootstrap.metrics_collector
            )
            metrics_retention_service = (
                metrics_retention_service
                if metrics_retention_service is not None
                else metrics_bootstrap.retention_service
            )
            config_service = (
                config_service
                if config_service is not None
                else metrics_bootstrap.config_service
            )

        self.metrics_service = metrics_service
        self.alert_service = alert_service
        self.metrics_collector = metrics_collector
        self.metrics_retention_service = metrics_retention_service
        self.config_service = config_service
        self._metrics_bootstrap = metrics_bootstrap

        self._state = GovernanceIntegrityRuntimeState.STOPPED
        self._started_at: Optional[datetime] = None
        self._worker_iterations = 0

    def status(
        self
    ) -> GovernanceIntegrityRuntimeStatus:

        uptime_seconds = 0

        if (
            self._started_at is not None
            and self._state == GovernanceIntegrityRuntimeState.RUNNING
        ):
            uptime_seconds = int(
                (
                    self.clock.now()
                    - self._started_at
                ).total_seconds()
            )

        active_dispatches = (
            self.scheduler
            .active_dispatch_count()
            if hasattr(self.scheduler, 'active_dispatch_count')
            else 0
        )

        return GovernanceIntegrityRuntimeStatus(

            state=
                self._state,

            started_at=
                self._started_at,

            uptime_seconds=
                uptime_seconds,

            worker_iterations=
                self._worker_iterations,

            active_dispatches=
                active_dispatches
        )

    def is_running(
        self
    ) -> bool:

        return self._state == GovernanceIntegrityRuntimeState.RUNNING

    def metrics(
        self
    ) -> GovernanceIntegrityMetrics:

        if self.metrics_service is None:

            return GovernanceIntegrityMetrics(
                total_dispatches=0,
                successful_dispatches=0,
                failed_dispatches=0,
                retry_dispatches=0,
                average_duration_ms=0.0
            )

        return self.metrics_service.snapshot()

    def active_alerts(
        self
    ) -> "tuple[GovernanceIntegrityMetricAlert, ...]":

        if self.alert_service is None:

            return ()

        return self.alert_service.active()

    def dashboard(
        self
    ) -> GovernanceIntegrityMetricsDashboard:

        if self.metrics_service is None:

            return GovernanceIntegrityMetricsDashboard(
                summary=self.metrics(),
                success_rate=0.0,
                failure_rate=0.0,
                retry_rate=0.0,
                active_alerts=0,
                last_updated=self.clock.now()
            )

        dashboard_service = GovernanceIntegrityMetricsDashboardService(
            self.metrics_service,
            alert_service=self.alert_service
        )

        return dashboard_service.overview()

    def metrics_bootstrap(
        self
    ) -> Optional[GovernanceIntegrityMetricsBootstrap]:

        return self._metrics_bootstrap

    def reload_config(
        self
    ) -> Optional[GovernanceIntegrityMetricsConfig]:
        """
        Re-read metrics configuration from its source and apply it
        to the collector, retention service, and metrics service,
        without restarting the runtime.

        Returns the newly loaded config, or None if no config
        service is configured (a no-op in that case).
        """

        if self.config_service is None:

            return None

        config = self.config_service.reload()

        if self.metrics_collector is not None:

            self.metrics_collector.reconfigure(
                interval_seconds=config.collection_interval_seconds
            )

        if self.metrics_retention_service is not None:

            from datetime import timedelta

            self.metrics_retention_service.reconfigure(
                max_age=timedelta(days=config.max_history_age_days),
                max_entries=config.max_history_entries
            )

        if self.metrics_service is not None:

            self.metrics_service.set_auto_flush_enabled(
                config.auto_flush
            )

        return config

    def _evaluate_alerts(
        self
    ):

        if self.alert_service is None or self.metrics_service is None:

            return

        self.alert_service.evaluate(self.metrics_service.snapshot())

    def start(
        self
    ):

        if self._state != GovernanceIntegrityRuntimeState.STOPPED:

            raise RuntimeError(
                "runtime is already running or transitioning"
            )

        self._state = (
            GovernanceIntegrityRuntimeState.STARTING
        )

        self._validate_providers()

        self.reload_config()

        if self.metrics_service is not None:

            self.metrics_service.load()

        self._evaluate_alerts()

        if self.metrics_collector is not None:

            self.metrics_collector.start()

        if self.log_rotation_service is not None:

            self.log_rotation_service.rotate()

        self._state = (
            GovernanceIntegrityRuntimeState.RUNNING
        )

        self._started_at = self.clock.now()

        if self.logger is not None:

            self.logger.info(
                "delivery_runtime", "runtime_started"
            )

    def stop(
        self
    ):

        if self._state == GovernanceIntegrityRuntimeState.STOPPED:

            return

        self._state = (
            GovernanceIntegrityRuntimeState.STOPPING
        )

        if self.metrics_collector is not None:

            self.metrics_collector.stop()

        if self.metrics_service is not None:

            self.metrics_service.flush()

        self._evaluate_alerts()

        self._state = (
            GovernanceIntegrityRuntimeState.STOPPED
        )

        self._started_at = None

        if self.logger is not None:

            self.logger.info(
                "delivery_runtime", "runtime_stopped"
            )

    def run_iteration(
        self
    ):

        if not self.is_running():

            raise RuntimeError(
                "runtime is not running"
            )

        try:

            self.worker.run_once()

        except Exception:

            if self.logger is not None:

                self.logger.exception(
                    "delivery_runtime", "worker_iteration_failed"
                )

            raise

        self._worker_iterations += 1

        if self.metrics_service is not None:

            self.metrics_service.flush()

        self._evaluate_alerts()

    def _validate_providers(
        self
    ):

        if self.provider_registry is None:

            raise ValueError(
                "provider registry is required"
            )

        if not hasattr(
            self.provider_registry,
            'list_providers'
        ):

            raise ValueError(
                "provider registry must have list_providers method"
            )

        providers = (
            self
            .provider_registry
            .list_providers()
        )

        if providers is None:

            raise ValueError(
                "provider registry returned None"
            )


def build_integrity_delivery_runtime(
    worker,
    scheduler,
    provider_registry,
    clock=None,
    metrics_service=None,
    alert_service=None,
    metrics_collector=None,
    metrics_retention_service=None,
    config_service=None,
    metrics_bootstrap=None,
    logger=None,
    log_repository=None,
    log_rotation_service=None
) -> GovernanceIntegrityDeliveryRuntime:

    if clock is None:

        from datetime import datetime, timezone

        class DefaultClock:

            def now(
                self
            ):

                return datetime.now(
                    timezone.utc
                )

        clock = DefaultClock()

    if worker is None:

        raise ValueError(
            "worker is required"
        )

    if scheduler is None:

        raise ValueError(
            "scheduler is required"
        )

    if provider_registry is None:

        raise ValueError(
            "provider_registry is required"
        )

    return GovernanceIntegrityDeliveryRuntime(

        worker=
            worker,

        scheduler=
            scheduler,

        provider_registry=
            provider_registry,

        clock=
            clock,

        metrics_service=
            metrics_service,

        alert_service=
            alert_service,

        metrics_collector=
            metrics_collector,

        metrics_retention_service=
            metrics_retention_service,

        config_service=
            config_service,

        metrics_bootstrap=
            metrics_bootstrap,

        logger=
            logger,

        log_repository=
            log_repository,

        log_rotation_service=
            log_rotation_service
    )
