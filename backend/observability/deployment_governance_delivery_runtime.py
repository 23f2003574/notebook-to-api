from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .deployment_governance_metrics import (
    GovernanceIntegrityMetrics,
    GovernanceIntegrityMetricsService,
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
        metrics_service: Optional[GovernanceIntegrityMetricsService] = None
    ):
        self.worker = worker
        self.scheduler = scheduler
        self.provider_registry = provider_registry
        self.clock = clock
        self.metrics_service = metrics_service

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

        if self.metrics_service is not None:

            self.metrics_service.load()

        self._state = (
            GovernanceIntegrityRuntimeState.RUNNING
        )

        self._started_at = self.clock.now()

    def stop(
        self
    ):

        if self._state == GovernanceIntegrityRuntimeState.STOPPED:

            return

        self._state = (
            GovernanceIntegrityRuntimeState.STOPPING
        )

        if self.metrics_service is not None:

            self.metrics_service.flush()

        self._state = (
            GovernanceIntegrityRuntimeState.STOPPED
        )

        self._started_at = None

    def run_iteration(
        self
    ):

        if not self.is_running():

            raise RuntimeError(
                "runtime is not running"
            )

        self.worker.run_once()

        self._worker_iterations += 1

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
    metrics_service=None
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
            metrics_service
    )
