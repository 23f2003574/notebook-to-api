import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock

from backend.observability.deployment_governance_delivery_runtime import (
    GovernanceIntegrityRuntimeState,
    GovernanceIntegrityRuntimeStatus,
    GovernanceIntegrityDeliveryRuntime,
    build_integrity_delivery_runtime
)
from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetricsService,
)
from backend.observability.deployment_governance_metrics_alerts import (
    GovernanceIntegrityMetricsAlertService,
)


class TestGovernanceIntegrityRuntimeStatus:

    def test_status_validation_timezone_aware(
        self
    ):

        with pytest.raises(ValueError):

            GovernanceIntegrityRuntimeStatus(

                state=
                    GovernanceIntegrityRuntimeState.RUNNING,

                started_at=
                    datetime.now(),

                uptime_seconds=
                    0,

                worker_iterations=
                    0,

                active_dispatches=
                    0
            )

    def test_status_validation_uptime_non_negative(
        self
    ):

        with pytest.raises(ValueError):

            GovernanceIntegrityRuntimeStatus(

                state=
                    GovernanceIntegrityRuntimeState.RUNNING,

                started_at=
                    datetime.now(
                        timezone.utc
                    ),

                uptime_seconds=
                    -1,

                worker_iterations=
                    0,

                active_dispatches=
                    0
            )

    def test_status_validation_worker_iterations_non_negative(
        self
    ):

        with pytest.raises(ValueError):

            GovernanceIntegrityRuntimeStatus(

                state=
                    GovernanceIntegrityRuntimeState.RUNNING,

                started_at=
                    datetime.now(
                        timezone.utc
                    ),

                uptime_seconds=
                    0,

                worker_iterations=
                    -1,

                active_dispatches=
                    0
            )

    def test_status_validation_active_dispatches_non_negative(
        self
    ):

        with pytest.raises(ValueError):

            GovernanceIntegrityRuntimeStatus(

                state=
                    GovernanceIntegrityRuntimeState.RUNNING,

                started_at=
                    datetime.now(
                        timezone.utc
                    ),

                uptime_seconds=
                    0,

                worker_iterations=
                    0,

                active_dispatches=
                    -1
            )

    def test_status_valid(
        self
    ):

        status = GovernanceIntegrityRuntimeStatus(

            state=
                GovernanceIntegrityRuntimeState.RUNNING,

            started_at=
                datetime.now(
                    timezone.utc
                ),

            uptime_seconds=
                100,

            worker_iterations=
                5,

            active_dispatches=
                3
        )

        assert status.state == GovernanceIntegrityRuntimeState.RUNNING
        assert status.uptime_seconds == 100
        assert status.worker_iterations == 5
        assert status.active_dispatches == 3


class TestGovernanceIntegrityDeliveryRuntime:

    def test_initial_status_stopped(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        status = runtime.status()

        assert status.state == GovernanceIntegrityRuntimeState.STOPPED
        assert status.started_at is None
        assert status.uptime_seconds == 0
        assert status.worker_iterations == 0
        assert status.active_dispatches == 0

    def test_start_transitions_to_running(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(
            timezone.utc
        )

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        runtime.start()

        status = runtime.status()

        assert status.state == GovernanceIntegrityRuntimeState.RUNNING
        assert status.started_at is not None
        assert runtime.is_running()

    def test_double_start_raises_error(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        runtime.start()

        with pytest.raises(
            RuntimeError,
            match="already running"
        ):

            runtime.start()

    def test_stop_transitions_to_stopped(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(
            timezone.utc
        )

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        runtime.start()
        runtime.stop()

        status = runtime.status()

        assert status.state == GovernanceIntegrityRuntimeState.STOPPED
        assert status.started_at is None
        assert not runtime.is_running()

    def test_stop_idempotent(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        runtime.stop()
        runtime.stop()

        status = runtime.status()

        assert status.state == GovernanceIntegrityRuntimeState.STOPPED

    def test_stop_from_stopping_idempotent(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        runtime._state = (
            GovernanceIntegrityRuntimeState.STOPPING
        )

        runtime.stop()

        status = runtime.status()

        assert status.state == GovernanceIntegrityRuntimeState.STOPPED

    def test_run_iteration_when_not_running_raises_error(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        with pytest.raises(
            RuntimeError,
            match="not running"
        ):

            runtime.run_iteration()

    def test_run_iteration_calls_worker(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(
            timezone.utc
        )

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        runtime.start()
        runtime.run_iteration()

        worker.run_once.assert_called_once()

        status = runtime.status()

        assert status.worker_iterations == 1

    def test_run_iteration_increments_counter(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(
            timezone.utc
        )

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        runtime.start()

        for _ in range(5):

            runtime.run_iteration()

        status = runtime.status()

        assert status.worker_iterations == 5
        assert worker.run_once.call_count == 5

    def test_uptime_calculation(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()

        start_time = datetime.now(
            timezone.utc
        )
        clock.now.return_value = start_time

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        runtime.start()

        clock.now.return_value = start_time + timedelta(
            seconds=10
        )

        status = runtime.status()

        assert status.uptime_seconds == 10

    def test_provider_validation_missing_registry_raises_error(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                None,

            clock=
                clock
        )

        with pytest.raises(
            ValueError,
            match="provider registry is required"
        ):

            runtime.start()

    def test_provider_validation_missing_list_providers_raises_error(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        del provider_registry.list_providers
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        with pytest.raises(
            ValueError,
            match="must have list_providers method"
        ):

            runtime.start()

    def test_provider_validation_none_return_raises_error(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        provider_registry.list_providers.return_value = None
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        with pytest.raises(
            ValueError,
            match="returned None"
        ):

            runtime.start()

    def test_active_dispatches_from_scheduler(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        scheduler.active_dispatch_count.return_value = 7
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(
            timezone.utc
        )

        runtime = GovernanceIntegrityDeliveryRuntime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        runtime.start()

        status = runtime.status()

        assert status.active_dispatches == 7


class TestBuildIntegrityDeliveryRuntime:

    def test_build_runtime_with_all_dependencies(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = build_integrity_delivery_runtime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry,

            clock=
                clock
        )

        assert isinstance(
            runtime,
            GovernanceIntegrityDeliveryRuntime
        )
        assert runtime.worker == worker
        assert runtime.scheduler == scheduler
        assert runtime.provider_registry == provider_registry
        assert runtime.clock == clock

    def test_build_runtime_with_default_clock(
        self
    ):

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()

        runtime = build_integrity_delivery_runtime(

            worker=
                worker,

            scheduler=
                scheduler,

            provider_registry=
                provider_registry
        )

        assert isinstance(
            runtime,
            GovernanceIntegrityDeliveryRuntime
        )
        assert runtime.clock is not None

    def test_build_runtime_missing_worker_raises_error(
        self
    ):

        scheduler = Mock()
        provider_registry = Mock()

        with pytest.raises(
            ValueError,
            match="worker is required"
        ):

            build_integrity_delivery_runtime(

                worker=
                    None,

                scheduler=
                    scheduler,

                provider_registry=
                    provider_registry
            )

    def test_build_runtime_missing_scheduler_raises_error(
        self
    ):

        worker = Mock()
        provider_registry = Mock()

        with pytest.raises(
            ValueError,
            match="scheduler is required"
        ):

            build_integrity_delivery_runtime(

                worker=
                    worker,

                scheduler=
                    None,

                provider_registry=
                    provider_registry
            )

    def test_build_runtime_missing_provider_registry_raises_error(
        self
    ):

        worker = Mock()
        scheduler = Mock()

        with pytest.raises(
            ValueError,
            match="provider_registry is required"
        ):

            build_integrity_delivery_runtime(

                worker=
                    worker,

                scheduler=
                    scheduler,

                provider_registry=
                    None
            )


class TestGovernanceIntegrityDeliveryRuntimeMetricsPersistence:

    def test_start_loads_metrics_from_service(self):
        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(timezone.utc)

        metrics_service = Mock(spec=GovernanceIntegrityMetricsService)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            metrics_service=metrics_service,
        )

        runtime.start()

        metrics_service.load.assert_called_once()

    def test_stop_flushes_metrics_to_service(self):
        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(timezone.utc)

        metrics_service = Mock(spec=GovernanceIntegrityMetricsService)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            metrics_service=metrics_service,
        )

        runtime.start()
        runtime.stop()

        metrics_service.flush.assert_called_once()

    def test_stop_without_start_does_not_flush(self):
        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        metrics_service = Mock(spec=GovernanceIntegrityMetricsService)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            metrics_service=metrics_service,
        )

        runtime.stop()

        metrics_service.flush.assert_not_called()

    def test_metrics_without_service_returns_empty_snapshot(self):
        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
        )

        metrics = runtime.metrics()

        assert metrics.total_dispatches == 0
        assert metrics.average_duration_ms == 0.0

    def test_metrics_delegates_to_service_snapshot(self):
        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        metrics_service = Mock(spec=GovernanceIntegrityMetricsService)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            metrics_service=metrics_service,
        )

        result = runtime.metrics()

        metrics_service.snapshot.assert_called_once()
        assert result == metrics_service.snapshot.return_value

    def test_run_iteration_flushes_metrics_periodically(self):
        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(timezone.utc)

        metrics_service = Mock(spec=GovernanceIntegrityMetricsService)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            metrics_service=metrics_service,
        )

        runtime.start()
        metrics_service.flush.reset_mock()

        runtime.run_iteration()
        runtime.run_iteration()

        assert metrics_service.flush.call_count == 2

    def test_run_iteration_without_metrics_service_does_not_raise(
        self,
    ):
        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(timezone.utc)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
        )

        runtime.start()
        runtime.run_iteration()


class TestGovernanceIntegrityDeliveryRuntimeAlerts:

    def test_active_alerts_without_alert_service_is_empty(self):
        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
        )

        assert runtime.active_alerts() == ()

    def test_active_alerts_delegates_to_alert_service(self):
        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        alert_service = Mock(spec=GovernanceIntegrityMetricsAlertService)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            alert_service=alert_service,
        )

        result = runtime.active_alerts()

        alert_service.active.assert_called_once()
        assert result == alert_service.active.return_value

    def test_run_iteration_evaluates_alerts(self):
        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(timezone.utc)

        metrics_service = Mock(spec=GovernanceIntegrityMetricsService)
        alert_service = Mock(spec=GovernanceIntegrityMetricsAlertService)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            metrics_service=metrics_service,
            alert_service=alert_service,
        )

        runtime.start()
        alert_service.evaluate.reset_mock()

        runtime.run_iteration()

        alert_service.evaluate.assert_called_once_with(
            metrics_service.snapshot.return_value
        )

    def test_stop_evaluates_alerts(self):
        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(timezone.utc)

        metrics_service = Mock(spec=GovernanceIntegrityMetricsService)
        alert_service = Mock(spec=GovernanceIntegrityMetricsAlertService)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            metrics_service=metrics_service,
            alert_service=alert_service,
        )

        runtime.start()
        alert_service.evaluate.reset_mock()

        runtime.stop()

        alert_service.evaluate.assert_called_once_with(
            metrics_service.snapshot.return_value
        )

    def test_start_evaluates_alerts(self):
        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(timezone.utc)

        metrics_service = Mock(spec=GovernanceIntegrityMetricsService)
        alert_service = Mock(spec=GovernanceIntegrityMetricsAlertService)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            metrics_service=metrics_service,
            alert_service=alert_service,
        )

        runtime.start()

        alert_service.evaluate.assert_called_once_with(
            metrics_service.snapshot.return_value
        )

    def test_alerts_use_real_service_end_to_end(self):
        worker = Mock()
        scheduler = Mock()
        del scheduler.active_dispatch_count
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = datetime.now(timezone.utc)

        metrics_service = GovernanceIntegrityMetricsService()
        alert_service = GovernanceIntegrityMetricsAlertService()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            metrics_service=metrics_service,
            alert_service=alert_service,
        )

        runtime.start()

        for _ in range(9):
            metrics_service.record_failure(10.0)

        metrics_service.record_success(10.0)

        runtime.run_iteration()

        active_names = {
            alert.name for alert in runtime.active_alerts()
        }

        assert "failure_rate" in active_names
