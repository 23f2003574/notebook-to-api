from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from backend.observability.deployment_governance_audit import (
    GovernanceAuditService,
)
from backend.observability.deployment_governance_blue_green import (
    BlueGreenDeploymentEngine,
)
from backend.observability.deployment_governance_canary import (
    CanaryDeploymentEngine,
)
from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_progressive_delivery import (
    ProgressiveDeliveryEngine,
)
from backend.observability.deployment_governance_rollback import (
    DeploymentRollbackEngine,
)
from backend.observability.deployment_governance_rolling import (
    RollingDeploymentEngine,
)
from backend.observability.deployment_governance_rollout_analytics import (
    DeploymentRolloutAnalytics,
)
from backend.observability.deployment_governance_rollout_bootstrap import (
    DeploymentRolloutBootstrap,
    DeploymentRolloutBootstrapError,
    RolloutBootstrapReport,
    RolloutBootstrapStatus,
    get_rollout_bootstrap,
)
from backend.observability.deployment_governance_rollout_dashboard import (
    DeploymentRolloutDashboard,
)
from backend.observability.deployment_governance_rollout_health import (
    DeploymentRolloutHealthEngine,
)
from backend.observability.deployment_governance_rollout_manager import (
    DeploymentRolloutManager,
)
from backend.observability.deployment_governance_rollout_policy import (
    DeploymentRolloutPolicyEngine,
)
from backend.observability.deployment_governance_scheduler import (
    GovernanceScheduler,
)
from backend.observability.deployment_governance_scheduler_metrics import (
    GovernanceSchedulerMetrics,
)
from backend.observability.deployment_governance_traffic_router import (
    DeploymentTrafficRouter,
)
from backend.observability.deployment_governance_version_registry import (
    DeploymentVersionRegistry,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


FULL_ROLLOUT_ORDER = (
    "version_registry",
    "traffic_router",
    "blue_green_engine",
    "canary_engine",
    "rolling_engine",
    "progressive_engine",
    "rollout_manager",
    "rollback_engine",
    "health_engine",
    "analytics",
    "policy_engine",
    "dashboard",
)


def _build_pipeline(clock=_clock, event_bus=None, scheduler=None) -> dict:
    """
    Build one fresh, fully-wired set of rollout-subsystem components
    (commits 1-12), independent from the process-wide singletons, in
    the same order and with the same post-construction setter wiring
    each component's own build_default_governance_* function uses.
    """

    event_bus = event_bus if event_bus is not None else GovernanceEventBus(
        clock=clock
    )
    scheduler = scheduler if scheduler is not None else GovernanceScheduler(
        clock=clock
    )
    metrics = GovernanceSchedulerMetrics(clock=clock)
    audit_service = GovernanceAuditService(clock=clock)

    version_registry = DeploymentVersionRegistry(
        clock=clock, event_bus=event_bus,
    )
    traffic_router = DeploymentTrafficRouter(
        clock=clock, event_bus=event_bus, metrics=metrics,
    )
    blue_green_engine = BlueGreenDeploymentEngine(
        clock=clock, event_bus=event_bus,
        version_registry=version_registry, traffic_router=traffic_router,
    )
    canary_engine = CanaryDeploymentEngine(
        clock=clock, event_bus=event_bus,
        version_registry=version_registry, scheduler=scheduler,
        metrics=metrics, traffic_router=traffic_router,
    )
    rolling_engine = RollingDeploymentEngine(
        clock=clock, event_bus=event_bus,
        version_registry=version_registry, scheduler=scheduler,
        metrics=metrics, traffic_router=traffic_router,
    )
    progressive_engine = ProgressiveDeliveryEngine(
        clock=clock, event_bus=event_bus, canary_engine=canary_engine,
        rolling_engine=rolling_engine, blue_green_engine=blue_green_engine,
        scheduler=scheduler, traffic_router=traffic_router,
    )
    rollout_manager = DeploymentRolloutManager(
        clock=clock, event_bus=event_bus,
        version_registry=version_registry,
        blue_green_engine=blue_green_engine, canary_engine=canary_engine,
        rolling_engine=rolling_engine, progressive_engine=progressive_engine,
        traffic_router=traffic_router,
    )
    rollback_engine = DeploymentRollbackEngine(
        clock=clock, event_bus=event_bus,
        version_registry=version_registry, traffic_router=traffic_router,
        rollout_manager=rollout_manager, blue_green_engine=blue_green_engine,
        canary_engine=canary_engine, rolling_engine=rolling_engine,
        progressive_engine=progressive_engine, audit_service=audit_service,
    )

    health_engine = DeploymentRolloutHealthEngine(
        clock=clock, event_bus=event_bus, metrics=metrics,
        rollback_engine=rollback_engine, scheduler=scheduler,
    )
    canary_engine.set_health_engine(health_engine)
    rolling_engine.set_health_engine(health_engine)
    progressive_engine.set_health_engine(health_engine)

    analytics = DeploymentRolloutAnalytics(
        clock=clock, event_bus=event_bus, rollout_manager=rollout_manager,
        health_engine=health_engine, rollback_engine=rollback_engine,
        traffic_router=traffic_router, metrics=metrics,
        audit_service=audit_service,
    )

    policy_engine = DeploymentRolloutPolicyEngine(
        clock=clock, event_bus=event_bus, audit_service=audit_service,
        analytics=analytics,
    )
    rollout_manager.set_policy_engine(policy_engine)
    traffic_router.set_policy_engine(policy_engine)
    rollback_engine.set_policy_engine(policy_engine)

    dashboard = DeploymentRolloutDashboard(
        clock=clock, event_bus=event_bus, rollout_manager=rollout_manager,
        version_registry=version_registry, traffic_router=traffic_router,
        health_engine=health_engine, rollback_engine=rollback_engine,
        analytics=analytics, policy_engine=policy_engine,
    )

    return dict(
        clock=clock,
        event_bus=event_bus,
        scheduler=scheduler,
        version_registry=version_registry,
        traffic_router=traffic_router,
        rollout_manager=rollout_manager,
        blue_green_engine=blue_green_engine,
        canary_engine=canary_engine,
        rolling_engine=rolling_engine,
        progressive_engine=progressive_engine,
        rollback_engine=rollback_engine,
        health_engine=health_engine,
        analytics=analytics,
        policy_engine=policy_engine,
        dashboard=dashboard,
    )


# --- Report / status dataclasses -------------------------------------------


class TestRolloutBootstrapReport:

    def test_rejects_naive_completed_at(self):
        with pytest.raises(
            ValueError, match="completed_at must be timezone-aware"
        ):
            RolloutBootstrapReport(
                started=True, initialized_components=(),
                registered_jobs=(), subscribed_events=(),
                completed_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_to_dict(self):
        report = RolloutBootstrapReport(
            started=True,
            initialized_components=("version_registry", "traffic_router"),
            registered_jobs=("rollout-progression",),
            subscribed_events=("rollout_started",),
            completed_at=BASE_TIME,
        )

        assert report.to_dict() == {
            "started": True,
            "initialized_components": ["version_registry", "traffic_router"],
            "registered_jobs": ["rollout-progression"],
            "subscribed_events": ["rollout_started"],
            "completed_at": BASE_TIME.isoformat(),
        }


class TestRolloutBootstrapStatus:

    def test_rejects_naive_started_at(self):
        with pytest.raises(
            ValueError, match="started_at must be timezone-aware"
        ):
            RolloutBootstrapStatus(
                initialized=True, version="1",
                started_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_to_dict_with_no_started_at(self):
        status = RolloutBootstrapStatus(
            initialized=False, version="1", started_at=None,
        )

        assert status.to_dict() == {
            "initialized": False, "version": "1", "started_at": None,
        }

    def test_to_dict_with_started_at(self):
        status = RolloutBootstrapStatus(
            initialized=True, version="1", started_at=BASE_TIME,
        )

        assert status.to_dict()["started_at"] == BASE_TIME.isoformat()


# --- Dependency validation ---------------------------------------------


class TestDeploymentRolloutBootstrapValidate:

    def test_fully_wired_pipeline_is_valid(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        result = bootstrap.validate()

        assert result.valid is True
        assert result.startup_order == FULL_ROLLOUT_ORDER

    def test_nothing_wired_is_trivially_valid(self):
        bootstrap = DeploymentRolloutBootstrap(clock=_clock)

        result = bootstrap.validate()

        assert result.valid is True
        assert result.startup_order == ()

    def test_gap_in_the_middle_is_reported_missing(self):
        components = _build_pipeline()
        components["rolling_engine"] = None

        bootstrap = DeploymentRolloutBootstrap(**components)

        result = bootstrap.validate()

        assert result.valid is False
        assert "rolling_engine" in result.missing

    def test_component_after_the_gap_is_never_registered(self):
        components = _build_pipeline()
        components["rollback_engine"] = None

        bootstrap = DeploymentRolloutBootstrap(**components)

        result = bootstrap.validate()

        assert result.valid is False
        assert "rollback_engine" in result.missing


# --- register_services() ------------------------------------------------


class TestDeploymentRolloutBootstrapRegisterServices:

    def test_returns_every_wired_component_in_order(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        registered = bootstrap.register_services()

        assert registered == FULL_ROLLOUT_ORDER

    def test_partial_wiring_omits_unwired_components(self):
        components = _build_pipeline()
        components["policy_engine"] = None
        components["dashboard"] = None

        bootstrap = DeploymentRolloutBootstrap(**components)
        registered = bootstrap.register_services()

        assert registered == FULL_ROLLOUT_ORDER[:-2]

    def test_invalid_graph_raises(self):
        components = _build_pipeline()
        components["canary_engine"] = None

        bootstrap = DeploymentRolloutBootstrap(**components)

        with pytest.raises(DeploymentRolloutBootstrapError):
            bootstrap.register_services()


# --- register_api() ------------------------------------------------------


class TestDeploymentRolloutBootstrapRegisterApi:

    def test_reports_the_governance_prefix_is_mounted(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        assert bootstrap.register_api() is True


# --- register_scheduler_jobs() -------------------------------------------


class TestDeploymentRolloutBootstrapRegisterSchedulerJobs:

    def test_registers_the_five_declared_jobs(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        jobs = bootstrap.register_scheduler_jobs()

        assert set(jobs) == {
            "rollout-progression",
            "rollout-health-evaluation",
            "rollout-analytics-aggregation",
            "rollout-rollback-trigger-evaluation",
            "rollout-dashboard-cache-refresh",
        }

    def test_no_scheduler_wired_returns_empty_tuple(self):
        components = _build_pipeline()
        components["scheduler"] = None

        bootstrap = DeploymentRolloutBootstrap(**components)

        assert bootstrap.register_scheduler_jobs() == ()

    def test_idempotent_does_not_reregister(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        first = bootstrap.register_scheduler_jobs()
        second = bootstrap.register_scheduler_jobs()

        assert set(first) == set(second)
        assert len(bootstrap._job_ids) == 5

    def test_concurrent_calls_register_each_job_exactly_once(self):
        components = _build_pipeline()
        bootstrap = DeploymentRolloutBootstrap(**components)

        def run():
            bootstrap.register_scheduler_jobs()

        threads = [threading.Thread(target=run) for _ in range(20)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(bootstrap._job_ids) == 5


# --- register_event_handlers() -------------------------------------------


class TestDeploymentRolloutBootstrapRegisterEventHandlers:

    def test_subscribes_to_every_tracked_event(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        subscribed = bootstrap.register_event_handlers()

        assert set(subscribed) == {
            "rollout_started",
            "rollout_completed",
            "rollout_failed",
            "routing_updated",
            "rollout_health_evaluated",
            "rollback_completed",
            "rollout_policy_denied",
            "rollout_analytics_updated",
        }

    def test_no_event_bus_wired_returns_empty_tuple(self):
        components = _build_pipeline()
        components["event_bus"] = None

        bootstrap = DeploymentRolloutBootstrap(**components)

        assert bootstrap.register_event_handlers() == ()

    def test_idempotent_does_not_resubscribe(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        bootstrap.register_event_handlers()
        bootstrap.register_event_handlers()

        assert len(bootstrap._subscriptions) == 8

    def test_tracked_event_updates_last_event_at(self):
        components = _build_pipeline()
        bus = components["event_bus"]
        bootstrap = DeploymentRolloutBootstrap(**components)
        bootstrap.register_event_handlers()

        assert bootstrap.last_event_at("rollout_started") is None

        bus.publish("rollout_started", source="test")

        assert bootstrap.last_event_at("rollout_started") == BASE_TIME


# --- initialize() ---------------------------------------------------------


class TestDeploymentRolloutBootstrapInitialize:

    def test_successful_bootstrap_reports_every_component(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        report = bootstrap.initialize()

        assert report.started is True
        assert report.initialized_components == FULL_ROLLOUT_ORDER
        assert len(report.registered_jobs) == 5
        assert len(report.subscribed_events) == 8

    def test_invalid_graph_raises_and_registers_nothing(self):
        components = _build_pipeline()
        components["health_engine"] = None

        bootstrap = DeploymentRolloutBootstrap(**components)

        with pytest.raises(DeploymentRolloutBootstrapError):
            bootstrap.initialize()

        assert bootstrap._job_ids == {}
        assert bootstrap._subscriptions == []

    def test_failed_event_is_published_on_invalid_graph(self):
        received: "list[str]" = []
        components = _build_pipeline()
        components["event_bus"].subscribe_all(
            lambda event: received.append(event.event_type)
        )
        components["canary_engine"] = None

        bootstrap = DeploymentRolloutBootstrap(**components)

        with pytest.raises(DeploymentRolloutBootstrapError):
            bootstrap.initialize()

        assert "rollout_bootstrap_started" in received
        assert "rollout_bootstrap_failed" in received
        assert "rollout_bootstrap_completed" not in received

    def test_success_events_are_published_in_order(self):
        received: "list[str]" = []
        components = _build_pipeline()
        components["event_bus"].subscribe_all(
            lambda event: received.append(event.event_type)
        )

        bootstrap = DeploymentRolloutBootstrap(**components)
        bootstrap.initialize()

        assert received == [
            "rollout_bootstrap_started",
            "rollout_bootstrap_completed",
            "rollout_runtime_ready",
        ]


# --- Idempotency -------------------------------------------------------


class TestDeploymentRolloutBootstrapIdempotency:

    def test_second_initialize_call_returns_the_cached_report(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        first_report = bootstrap.initialize()
        second_report = bootstrap.initialize()

        assert second_report is first_report

    def test_second_initialize_call_publishes_nothing_further(self):
        components = _build_pipeline()
        bootstrap = DeploymentRolloutBootstrap(**components)
        bootstrap.initialize()

        received: "list[str]" = []
        components["event_bus"].subscribe_all(
            lambda event: received.append(event.event_type)
        )

        bootstrap.initialize()

        assert received == []

    def test_second_shutdown_call_is_a_no_op(self):
        components = _build_pipeline()
        bootstrap = DeploymentRolloutBootstrap(**components)
        bootstrap.initialize()
        bootstrap.shutdown()

        received: "list[str]" = []
        components["event_bus"].subscribe_all(
            lambda event: received.append(event.event_type)
        )

        bootstrap.shutdown()

        assert received == []

    def test_concurrent_initialize_calls_register_jobs_exactly_once(self):
        components = _build_pipeline()
        bootstrap = DeploymentRolloutBootstrap(**components)

        errors: "list[Exception]" = []

        def run():
            try:
                bootstrap.initialize()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(20)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert errors == []
        assert len(bootstrap._job_ids) == 5
        assert len(bootstrap._subscriptions) == 8


# --- shutdown() -------------------------------------------------------


class TestDeploymentRolloutBootstrapShutdown:

    def test_shutdown_when_not_initialized_is_a_no_op(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        bootstrap.shutdown()

        assert bootstrap.status().initialized is False

    def test_shutdown_unregisters_scheduler_jobs(self):
        components = _build_pipeline()
        scheduler = components["scheduler"]
        bootstrap = DeploymentRolloutBootstrap(**components)
        bootstrap.initialize()

        job_ids = list(bootstrap._job_ids.values())
        bootstrap.shutdown()

        for job_id in job_ids:
            with pytest.raises(KeyError):
                scheduler.unregister(job_id)

    def test_shutdown_unsubscribes_event_handlers(self):
        components = _build_pipeline()
        bus = components["event_bus"]
        bootstrap = DeploymentRolloutBootstrap(**components)
        bootstrap.initialize()

        bootstrap.shutdown()

        received: "list[str]" = []
        bus.subscribe_all(lambda event: received.append(event.event_type))
        bus.publish("rollout_started", source="test")

        assert bootstrap.last_event_at("rollout_started") is None

    def test_shutdown_publishes_runtime_shutdown_event(self):
        components = _build_pipeline()
        bootstrap = DeploymentRolloutBootstrap(**components)
        bootstrap.initialize()

        received: "list[str]" = []
        components["event_bus"].subscribe_all(
            lambda event: received.append(event.event_type)
        )

        bootstrap.shutdown()

        assert received == [
            "rollout_dashboard_refreshed", "rollout_runtime_shutdown",
        ]

    def test_shutdown_sets_initialized_false(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())
        bootstrap.initialize()

        bootstrap.shutdown()

        assert bootstrap.status().initialized is False


# --- restart() ---------------------------------------------------------


class TestDeploymentRolloutBootstrapRestart:

    def test_restart_reinitializes_after_shutdown(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())
        bootstrap.initialize()

        report = bootstrap.restart()

        assert report.started is True
        assert bootstrap.status().initialized is True

    def test_restart_produces_a_fresh_report(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        first_report = bootstrap.initialize()
        second_report = bootstrap.restart()

        assert second_report is not first_report


# --- status() / health_check() -------------------------------------------


class TestDeploymentRolloutBootstrapStatus:

    def test_status_before_initialize(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        status = bootstrap.status()

        assert status.initialized is False
        assert status.started_at is None

    def test_status_after_initialize(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        bootstrap.initialize()
        status = bootstrap.status()

        assert status.initialized is True
        assert status.started_at == BASE_TIME


class TestDeploymentRolloutBootstrapHealthCheck:

    def test_unhealthy_before_initialize(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())

        healthy, reason = bootstrap.health_check()

        assert healthy is False
        assert reason == "rollout bootstrap has not been initialized"

    def test_healthy_after_initialize(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())
        bootstrap.initialize()

        healthy, reason = bootstrap.health_check()

        assert healthy is True
        assert reason is None

    def test_unhealthy_after_shutdown(self):
        bootstrap = DeploymentRolloutBootstrap(**_build_pipeline())
        bootstrap.initialize()
        bootstrap.shutdown()

        healthy, _ = bootstrap.health_check()

        assert healthy is False


# --- Dependency injection -------------------------------------------------


class TestDeploymentRolloutBootstrapDependencyInjection:

    def test_missing_scheduler_still_initializes_without_jobs(self):
        components = _build_pipeline()
        components["scheduler"] = None

        bootstrap = DeploymentRolloutBootstrap(**components)
        report = bootstrap.initialize()

        assert report.started is True
        assert report.registered_jobs == ()

    def test_missing_event_bus_still_initializes_without_subscriptions(
        self,
    ):
        components = _build_pipeline()
        components["event_bus"] = None

        bootstrap = DeploymentRolloutBootstrap(**components)
        report = bootstrap.initialize()

        assert report.started is True
        assert report.subscribed_events == ()


# --- Singleton -------------------------------------------------------------


class TestDeploymentRolloutBootstrapSingleton:

    def test_get_rollout_bootstrap_returns_same_instance(self):
        assert get_rollout_bootstrap() is get_rollout_bootstrap()

    def test_default_bootstrap_wires_every_real_singleton(self):
        from backend.observability.deployment_governance_rollout_manager import (
            get_rollout_manager,
        )
        from backend.observability.deployment_governance_version_registry import (
            get_version_registry,
        )

        bootstrap = get_rollout_bootstrap()

        assert bootstrap._rollout_manager is get_rollout_manager()
        assert bootstrap._version_registry is get_version_registry()

    def test_default_bootstrap_is_not_pre_initialized(self):
        from backend.observability.deployment_governance_rollout_bootstrap import (
            build_default_governance_rollout_bootstrap,
        )

        fresh = build_default_governance_rollout_bootstrap()

        assert fresh.status().initialized is False


# --- Runtime integration (real singleton, full lifecycle) -----------------


class TestDeploymentRolloutBootstrapRuntimeIntegration:

    def test_singleton_initialize_and_shutdown_round_trip(self):
        bootstrap = get_rollout_bootstrap()

        try:
            report = bootstrap.initialize()

            assert report.started is True
            assert bootstrap.health_check() == (True, None)

        finally:
            bootstrap.shutdown()

        assert bootstrap.status().initialized is False

    def test_lifecycle_manager_starts_and_stops_the_rollout_subsystem(self):
        from backend.observability.deployment_governance_lifecycle import (
            get_lifecycle_manager,
        )

        manager = get_lifecycle_manager()
        bootstrap = get_rollout_bootstrap()

        try:
            manager.startup()

            assert bootstrap.status().initialized is True

            manager.shutdown()

            assert bootstrap.status().initialized is False

        finally:
            bootstrap.shutdown()
