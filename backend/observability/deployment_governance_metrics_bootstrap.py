from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from .deployment_governance_metrics_alerts import (
    GovernanceIntegrityMetricsAlertService,
)
from .deployment_governance_metrics_api import (
    GovernanceIntegrityMetricsApi,
)
from .deployment_governance_metrics_collector import (
    GovernanceIntegrityMetricsCollector,
)
from .deployment_governance_metrics_config import (
    GovernanceIntegrityMetricsConfigService,
)
from .deployment_governance_metrics_dashboard import (
    GovernanceIntegrityMetricsDashboardService,
)
from .deployment_governance_metrics_retention import (
    GovernanceIntegrityMetricsRetentionService,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .deployment_governance_persistence import (
        DeploymentGovernancePersistenceRuntime,
    )
    from .deployment_governance_event_bus import GovernanceEventBus


@dataclass(frozen=True)
class GovernanceIntegrityMetricsBootstrapHealth:
    """
    A point-in-time snapshot of the metrics bootstrap's own
    lifecycle state, distinct from the governance metrics it wires
    together: this describes whether the subsystem itself is built,
    initialized, and running, not what it has recorded.
    """

    built: bool

    initialized: bool

    collector_running: bool

    active_alerts: int

    @property
    def is_healthy(self) -> bool:
        """
        Whether the subsystem has completed its full lifecycle
        (built and initialized). Does not consider active_alerts:
        those reflect the metrics being recorded, not the bootstrap's
        own operability.
        """

        return self.built and self.initialized

    def to_dict(self) -> dict[str, object]:
        return {
            "built": self.built,
            "initialized": self.initialized,
            "collector_running": self.collector_running,
            "active_alerts": self.active_alerts,
        }


class GovernanceIntegrityMetricsBootstrap:
    """
    Constructs, wires, and manages the lifecycle of every governance
    metrics component as a single unit, replacing the previous
    pattern of each CLI command and runtime independently
    constructing its own slice of the metrics subsystem.

    Lifecycle is two-phase and strict: build() constructs and wires
    every service (no side effects beyond object construction),
    initialize() then activates the subsystem (loads durable state,
    evaluates alerts, starts the background collector, and
    optionally registers the request-metrics middleware). Both
    phases are single-shot: calling either twice raises rather than
    silently reconstructing or restarting an already-live subsystem.
    shutdown() is safe to call at any point, including before
    build()/initialize(), or more than once.
    """

    def __init__(
        self,
        persistence_runtime: "DeploymentGovernancePersistenceRuntime",
        *,
        app: "FastAPI | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
    ) -> None:
        if persistence_runtime is None:
            raise ValueError(
                "persistence_runtime is required"
            )

        self._persistence_runtime = persistence_runtime

        self._app = app

        self._event_bus = event_bus

        self._built = False

        self._initialized = False

        self.config_service: (
            GovernanceIntegrityMetricsConfigService | None
        ) = None

        self.metrics_service = None

        self.alert_service: (
            GovernanceIntegrityMetricsAlertService | None
        ) = None

        self.retention_service: (
            GovernanceIntegrityMetricsRetentionService | None
        ) = None

        self.metrics_collector: (
            GovernanceIntegrityMetricsCollector | None
        ) = None

        self.dashboard_service: (
            GovernanceIntegrityMetricsDashboardService | None
        ) = None

        self.api: GovernanceIntegrityMetricsApi | None = None

    def build(self) -> "GovernanceIntegrityMetricsBootstrap":
        """
        Construct every metrics service and wire their dependencies
        together. Performs no I/O and starts nothing; call
        initialize() afterward to activate the subsystem.

        Raises RuntimeError if already built.
        """

        if self._built:
            raise RuntimeError(
                "metrics bootstrap has already been built"
            )

        self.config_service = GovernanceIntegrityMetricsConfigService()

        self.metrics_service = (
            self._persistence_runtime.build_integrity_metrics_service()
        )

        self.alert_service = (
            self._persistence_runtime
            .build_integrity_metrics_alert_service()
        )

        self.retention_service = GovernanceIntegrityMetricsRetentionService(
            self._persistence_runtime
            .build_integrity_metrics_history_repository()
        )

        self.metrics_collector = GovernanceIntegrityMetricsCollector(
            self.metrics_service,
            retention_service=self.retention_service,
        )

        self.dashboard_service = GovernanceIntegrityMetricsDashboardService(
            self.metrics_service,
            alert_service=self.alert_service,
        )

        self.api = GovernanceIntegrityMetricsApi(
            self.metrics_service,
            alert_service=self.alert_service,
        )

        self._built = True

        return self

    def initialize(self) -> None:
        """
        Activate the built subsystem: apply current configuration,
        load durable metrics state, run an initial alert evaluation,
        start the background collector, and register the request
        metrics middleware if an app was provided.

        Publishes a "metrics_snapshot_created" event for the snapshot
        loaded from durable storage, if this bootstrap was
        constructed with an event_bus.

        Raises RuntimeError if build() has not run yet, or if
        already initialized.
        """

        if not self._built:
            raise RuntimeError(
                "metrics bootstrap must be built before it can be "
                "initialized"
            )

        if self._initialized:
            raise RuntimeError(
                "metrics bootstrap has already been initialized"
            )

        config = self.config_service.load()

        self.retention_service.reconfigure(
            max_age=timedelta(days=config.max_history_age_days),
            max_entries=config.max_history_entries,
        )

        self.metrics_collector.reconfigure(
            interval_seconds=config.collection_interval_seconds,
        )

        self.metrics_service.set_auto_flush_enabled(config.auto_flush)

        self.metrics_service.load()

        snapshot = self.metrics_service.snapshot()

        self.alert_service.evaluate(snapshot)

        if self._event_bus is not None:
            self._event_bus.publish(
                "metrics_snapshot_created",
                source="metrics_bootstrap",
                payload=snapshot.to_dict(),
            )

        self.metrics_collector.start()

        if self._app is not None:
            from .deployment_governance_api import (
                register_governance_metrics_middleware,
            )

            register_governance_metrics_middleware(self._app)

        self._initialized = True

    def shutdown(self) -> None:
        """
        Gracefully stop the background collector and flush current
        metrics to durable storage.

        Safe to call multiple times, and safe to call even if
        initialize() was never reached: both are no-ops in that
        case.
        """

        if not self._initialized:
            return

        if self.metrics_collector is not None:
            self.metrics_collector.stop()

        if self.metrics_service is not None:
            self.metrics_service.flush()

        self._initialized = False

    def health(self) -> GovernanceIntegrityMetricsBootstrapHealth:
        """
        Return the bootstrap's current lifecycle state.
        """

        return GovernanceIntegrityMetricsBootstrapHealth(
            built=self._built,
            initialized=self._initialized,
            collector_running=(
                self.metrics_collector is not None
                and self.metrics_collector.is_running()
            ),
            active_alerts=(
                0
                if self.alert_service is None
                else len(self.alert_service.active())
            ),
        )


def build_integrity_metrics_bootstrap(
    persistence_runtime: "DeploymentGovernancePersistenceRuntime",
    *,
    app: "FastAPI | None" = None,
) -> GovernanceIntegrityMetricsBootstrap:
    """
    Build (but do not initialize) a GovernanceIntegrityMetricsBootstrap
    for persistence_runtime.

    Raises ValueError if persistence_runtime is None.
    """

    return GovernanceIntegrityMetricsBootstrap(
        persistence_runtime, app=app
    ).build()
