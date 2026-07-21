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
from .deployment_governance_log_redaction import (
    GovernanceLogRedactionService,
)
from .deployment_governance_log_context import (
    GovernanceLogContext,
    GovernanceLogContextService,
)
from .deployment_governance_log_sampling import (
    GovernanceLogSamplingService,
)
from .deployment_governance_log_batcher import (
    GovernanceLogBatcher,
)
from .deployment_governance_log_config import (
    GovernanceLogConfig,
    GovernanceLogConfigService,
)
from .deployment_governance_logging_bootstrap import (
    GovernanceLoggingBootstrap,
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
        log_rotation_service: Optional[GovernanceLogRotationService] = None,
        redaction_service: Optional[GovernanceLogRedactionService] = None,
        context_service: Optional[GovernanceLogContextService] = None,
        sampling_service: Optional[GovernanceLogSamplingService] = None,
        batcher: Optional[GovernanceLogBatcher] = None,
        log_config_service: Optional[GovernanceLogConfigService] = None,
        logging_bootstrap: Optional[GovernanceLoggingBootstrap] = None
    ):
        self.worker = worker
        self.scheduler = scheduler
        self.provider_registry = provider_registry
        self.clock = clock

        # A given logging_bootstrap replaces the previous pattern of
        # wiring each logging-related dependency independently: any
        # of the eight below that were not explicitly passed are
        # filled in from it, but an explicit value always wins.
        # correlation_service/search_service/export_service/
        # replay_service are also on the bootstrap but have no
        # corresponding attribute here to back-fill; reach them via
        # logging_bootstrap() instead.
        if logging_bootstrap is not None:
            logger = (
                logger
                if logger is not None
                else logging_bootstrap.logger
            )
            log_repository = (
                log_repository
                if log_repository is not None
                else logging_bootstrap.log_repository
            )
            log_rotation_service = (
                log_rotation_service
                if log_rotation_service is not None
                else logging_bootstrap.log_rotation_service
            )
            redaction_service = (
                redaction_service
                if redaction_service is not None
                else logging_bootstrap.redaction_service
            )
            context_service = (
                context_service
                if context_service is not None
                else logging_bootstrap.context_service
            )
            sampling_service = (
                sampling_service
                if sampling_service is not None
                else logging_bootstrap.sampling_service
            )
            batcher = (
                batcher
                if batcher is not None
                else logging_bootstrap.batcher
            )
            log_config_service = (
                log_config_service
                if log_config_service is not None
                else logging_bootstrap.log_config_service
            )

        self.logger = logger
        self.log_repository = log_repository
        self.log_rotation_service = log_rotation_service
        self.redaction_service = redaction_service
        self.context_service = context_service
        self.sampling_service = sampling_service
        self.batcher = batcher
        self.log_config_service = log_config_service
        self._logging_bootstrap = logging_bootstrap

        # Wired immediately, not deferred to start(): redaction is a
        # security property of the logger and should take effect as
        # soon as both are configured together, regardless of
        # whether/when this runtime is started.
        if self.logger is not None and self.redaction_service is not None:
            self.logger.set_redaction_service(self.redaction_service)

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

    def logging_bootstrap(
        self
    ) -> Optional[GovernanceLoggingBootstrap]:

        return self._logging_bootstrap

    def build_health_service(
        self
    ) -> "GovernanceHealthService":
        """
        Build a GovernanceHealthService with checks registered for
        this runtime's own state and for the metrics bootstrap,
        logging bootstrap, and provider registry it wires together.

        Each check reflects whatever this runtime was constructed
        with: a component that was never wired in (e.g. no
        metrics_bootstrap passed to __init__) is reported unhealthy
        rather than omitted, since check_all()/summary() are meant to
        surface exactly the components a caller expected to be
        present.
        """

        from .deployment_governance_health import (
            GovernanceHealthService,
        )

        service = GovernanceHealthService(clock=self.clock.now)

        service.register(
            "delivery_runtime", self._check_delivery_runtime_health
        )

        service.register(
            "metrics_bootstrap", self._check_metrics_bootstrap_health
        )

        service.register(
            "logging_bootstrap", self._check_logging_bootstrap_health
        )

        service.register(
            "provider_registry", self._check_provider_registry_health
        )

        return service

    def _check_delivery_runtime_health(
        self
    ):

        if self.is_running():

            return True

        return False, f"delivery runtime is {self._state.value}"

    def _check_metrics_bootstrap_health(
        self
    ):

        if self._metrics_bootstrap is None:

            return False, "metrics bootstrap is not configured"

        health = self._metrics_bootstrap.health()

        if health.is_healthy:

            return True

        return False, (
            f"metrics bootstrap built={health.built} "
            f"initialized={health.initialized}"
        )

    def _check_logging_bootstrap_health(
        self
    ):

        if self._logging_bootstrap is None:

            return False, "logging bootstrap is not configured"

        health = self._logging_bootstrap.health()

        if (
            health.built
            and health.initialized
            and health.dependencies_valid
        ):

            return True

        return False, (
            f"logging bootstrap built={health.built} "
            f"initialized={health.initialized} "
            f"dependencies_valid={health.dependencies_valid}"
        )

    def _check_provider_registry_health(
        self
    ):

        if self.provider_registry is None:

            return False, "provider registry is not configured"

        if not hasattr(self.provider_registry, "health_all"):

            return True

        from .deployment_governance_provider_health import (
            GovernanceIntegrityProviderHealthStatus,
        )

        unhealthy = [
            status.channel_type.value
            for status in self.provider_registry.health_all()
            if status.status
            is not GovernanceIntegrityProviderHealthStatus.HEALTHY
        ]

        if unhealthy:

            return False, (
                "unhealthy providers: " + ", ".join(sorted(unhealthy))
            )

        return True

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

    def reload_log_config(
        self
    ) -> Optional[GovernanceLogConfig]:
        """
        Re-read governance logging configuration from its source and
        apply it to the logger (minimum level, and whether sampling
        and redaction are active) and the batcher (batch size, flush
        interval), without restarting the runtime.

        Returns the newly loaded config, or None if no
        log_config_service is configured (a no-op in that case).
        """

        if self.log_config_service is None:

            return None

        config = self.log_config_service.reload()

        if self.logger is not None:

            self.logger.set_minimum_level(config.minimum_level)

            self.logger.set_sampling_service(
                self.sampling_service
                if config.enable_sampling
                else None
            )

            self.logger.set_redaction_service(
                self.redaction_service
                if config.enable_redaction
                else None
            )

        if self.batcher is not None:

            self.batcher.reconfigure(
                batch_size=config.batch_size,
                flush_interval_seconds=(
                    config.flush_interval_seconds
                )
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

        if self.batcher is not None:

            # Unconditional, not flush_if_needed(): shutdown must
            # never leave an enqueued entry stranded in a batch that
            # never reached its size/interval threshold, including
            # the "runtime_stopped" entry logged just above.
            self.batcher.flush()

    def run_iteration(
        self
    ):

        if not self.is_running():

            raise RuntimeError(
                "runtime is not running"
            )

        if self.context_service is not None:

            self.context_service.push(
                GovernanceLogContext(
                    request_id=None,
                    dispatch_id=None,
                    provider=None,
                    component="delivery_runtime",
                )
            )

        try:

            try:

                self.worker.run_once()

            except Exception:

                if self.logger is not None:

                    self.logger.exception(
                        "delivery_runtime", "worker_iteration_failed"
                    )

                raise

            self._worker_iterations += 1

        finally:

            if self.context_service is not None:

                self.context_service.pop()

            if self.batcher is not None:

                # Checked every iteration regardless of the log
                # volume this particular iteration produced, so the
                # interval threshold is still caught during quiet
                # periods with few or no new entries.
                self.batcher.flush_if_needed()

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
    log_rotation_service=None,
    redaction_service=None,
    context_service=None,
    sampling_service=None,
    batcher=None,
    log_config_service=None,
    logging_bootstrap=None
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
            log_rotation_service,

        redaction_service=
            redaction_service,

        context_service=
            context_service,

        sampling_service=
            sampling_service,

        batcher=
            batcher,

        log_config_service=
            log_config_service,

        logging_bootstrap=
            logging_bootstrap
    )
