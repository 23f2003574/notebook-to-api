from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_blue_green import (
    BlueGreenDeploymentEngine,
)
from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_rollback import (
    DeploymentRollbackEngine,
)
from backend.observability.deployment_governance_rollout_analytics import (
    DeploymentRolloutAnalytics,
)
from backend.observability.deployment_governance_rollout_dashboard import (
    DeploymentDashboardEntry,
    DeploymentRolloutDashboard,
    RolloutDashboard,
    get_rollout_dashboard,
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
from backend.observability.deployment_governance_traffic_router import (
    DeploymentTrafficRouter,
)
from backend.observability.deployment_governance_version_registry import (
    DeploymentVersionRegistry,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)

VALID_CHECKSUM = "a" * 64


class _ManualClock:
    def __init__(self, start: datetime = BASE_TIME) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


def _clock():
    return BASE_TIME


def _dashboard(clock=_clock, **kwargs) -> DeploymentRolloutDashboard:
    return DeploymentRolloutDashboard(clock=clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The rollout dashboard is a process-wide singleton; most tests
    below construct their own fresh dashboard instead (see
    _dashboard), and only the singleton and API tests touch the
    shared instance. The dashboard itself has no state to reset (it
    is purely read-only), but the subsystems underneath it do,
    matching every other singleton test file's own fixture.
    """

    def _reset():
        from backend.observability.deployment_governance_rollback import (
            get_rollback_engine,
        )
        from backend.observability.deployment_governance_rollout_analytics import (  # noqa: E501
            get_rollout_analytics,
        )
        from backend.observability.deployment_governance_rollout_health import (  # noqa: E501
            get_rollout_health_engine,
        )
        from backend.observability.deployment_governance_rollout_manager import (  # noqa: E501
            get_rollout_manager,
        )
        from backend.observability.deployment_governance_rollout_policy import (  # noqa: E501
            get_rollout_policy_engine,
        )
        from backend.observability.deployment_governance_traffic_router import (  # noqa: E501
            get_traffic_router,
        )
        from backend.observability.deployment_governance_version_registry import (  # noqa: E501
            get_version_registry,
        )

        get_rollout_manager().clear()
        get_version_registry().clear()
        get_traffic_router().clear()
        get_rollout_health_engine().clear_history()
        get_rollback_engine().clear_history()
        get_rollout_analytics().reset()
        get_rollout_policy_engine().clear()

    _reset()
    yield
    _reset()


# --- Models ------------------------------------------------------------


class TestDeploymentDashboardEntry:

    def test_rejects_empty_deployment_id(self):
        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            DeploymentDashboardEntry(
                deployment_id="", version="1.0.0", strategy="CANARY",
                health="HEALTHY", traffic_percentage=100.0,
                state="RUNNING", updated_at=BASE_TIME,
            )

    def test_rejects_negative_traffic_percentage(self):
        with pytest.raises(
            ValueError, match="traffic_percentage must not be negative"
        ):
            DeploymentDashboardEntry(
                deployment_id="dep-1", version="1.0.0",
                strategy="CANARY", health="HEALTHY",
                traffic_percentage=-1.0, state="RUNNING",
                updated_at=BASE_TIME,
            )

    def test_rejects_naive_updated_at(self):
        with pytest.raises(
            ValueError, match="updated_at must be timezone-aware"
        ):
            DeploymentDashboardEntry(
                deployment_id="dep-1", version="1.0.0",
                strategy="CANARY", health="HEALTHY",
                traffic_percentage=100.0, state="RUNNING",
                updated_at=datetime(2026, 7, 23, 12, 0, 0),
            )

    def test_to_dict(self):
        entry = DeploymentDashboardEntry(
            deployment_id="dep-1", version="1.0.0", strategy="CANARY",
            health="HEALTHY", traffic_percentage=50.0, state="RUNNING",
            updated_at=BASE_TIME,
        )

        assert entry.to_dict() == {
            "deployment_id": "dep-1",
            "version": "1.0.0",
            "strategy": "CANARY",
            "health": "HEALTHY",
            "traffic_percentage": 50.0,
            "state": "RUNNING",
            "updated_at": BASE_TIME.isoformat(),
        }


class TestRolloutDashboard:

    def test_rejects_naive_generated_at(self):
        with pytest.raises(
            ValueError, match="generated_at must be timezone-aware"
        ):
            RolloutDashboard(
                generated_at=datetime(2026, 7, 23, 12, 0, 0),
                active_rollouts=0, completed_rollouts=0,
                failed_rollouts=0, deployments=(),
            )

    def test_rejects_negative_counts(self):
        with pytest.raises(ValueError, match="active_rollouts must be >= 0"):
            RolloutDashboard(
                generated_at=BASE_TIME, active_rollouts=-1,
                completed_rollouts=0, failed_rollouts=0, deployments=(),
            )

    def test_to_dict(self):
        dashboard = RolloutDashboard(
            generated_at=BASE_TIME, active_rollouts=1,
            completed_rollouts=2, failed_rollouts=0, deployments=(),
        )

        assert dashboard.to_dict() == {
            "generated_at": BASE_TIME.isoformat(),
            "active_rollouts": 1,
            "completed_rollouts": 2,
            "failed_rollouts": 0,
            "deployments": [],
        }


# --- Overview generation -----------------------------------------------


class TestOverviewGeneration:

    def test_empty_dashboard_with_nothing_wired(self):
        dashboard = _dashboard()

        overview = dashboard.overview()

        assert overview.active_rollouts == 0
        assert overview.completed_rollouts == 0
        assert overview.failed_rollouts == 0
        assert overview.deployments == ()

    def test_counts_rollouts_by_state(self):
        rollout_manager = DeploymentRolloutManager(clock=_clock)
        running = rollout_manager.create("dep-1", "CANARY")
        rollout_manager.start(running.rollout_id)

        completed = rollout_manager.create("dep-2", "CANARY")
        rollout_manager.start(completed.rollout_id)
        rollout_manager.complete(completed.rollout_id)

        failed = rollout_manager.create("dep-3", "CANARY")
        rollout_manager.start(failed.rollout_id)
        rollout_manager.fail(failed.rollout_id)

        dashboard = _dashboard(rollout_manager=rollout_manager)

        overview = dashboard.overview()

        assert overview.active_rollouts == 1
        assert overview.completed_rollouts == 1
        assert overview.failed_rollouts == 1

    def test_publishes_rollout_dashboard_generated(self):
        bus = GovernanceEventBus(clock=_clock)
        dashboard = _dashboard(event_bus=bus)

        events = []
        bus.subscribe("rollout_dashboard_generated", events.append)

        dashboard.overview()

        assert len(events) == 1


# --- Deployment aggregation ----------------------------------------------


class TestDeploymentAggregation:

    def test_aggregates_version_strategy_health_traffic_state(self):
        registry = DeploymentVersionRegistry(clock=_clock)
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        rollout_manager = DeploymentRolloutManager(clock=_clock)
        rollout = rollout_manager.create("dep-1", "CANARY")
        rollout_manager.start(rollout.rollout_id)

        router = DeploymentTrafficRouter(clock=_clock)
        router.configure(
            "dep-1", [("1.0.0", 60.0), ("1.1.0", 40.0)],
            strategy="CANARY",
        )

        health_engine = DeploymentRolloutHealthEngine(clock=_clock)
        health_engine.evaluate("dep-1")

        dashboard = _dashboard(
            rollout_manager=rollout_manager, version_registry=registry,
            traffic_router=router, health_engine=health_engine,
        )

        entries = dashboard.deployments()

        assert len(entries) == 1

        entry = entries[0]

        assert entry.deployment_id == "dep-1"
        assert entry.version == "1.0.0"
        assert entry.strategy == "CANARY"
        assert entry.state == "RUNNING"
        assert entry.health == "HEALTHY"
        assert entry.traffic_percentage == 60.0

    def test_unions_deployment_ids_across_subsystems(self):
        registry = DeploymentVersionRegistry(clock=_clock)
        registry.register("dep-registry-only", "1.0.0", "a.tar.gz", VALID_CHECKSUM)

        rollout_manager = DeploymentRolloutManager(clock=_clock)
        rollout_manager.create("dep-rollout-only", "CANARY")

        dashboard = _dashboard(
            rollout_manager=rollout_manager, version_registry=registry,
        )

        entries = dashboard.deployments()

        assert {e.deployment_id for e in entries} == {
            "dep-registry-only", "dep-rollout-only",
        }

    def test_missing_data_falls_back_to_placeholders(self):
        rollout_manager = DeploymentRolloutManager(clock=_clock)
        rollout_manager.create("dep-1", "CANARY")

        dashboard = _dashboard(rollout_manager=rollout_manager)

        entry = dashboard.deployments()[0]

        assert entry.version == ""
        assert entry.health == "UNKNOWN"
        assert entry.traffic_percentage == 0.0

    def test_traffic_percentage_falls_back_to_max_allocation(self):
        router = DeploymentTrafficRouter(clock=_clock)
        router.configure(
            "dep-1", [("1.0.0", 30.0), ("1.1.0", 70.0)],
        )

        dashboard = _dashboard(traffic_router=router)

        entry = dashboard.deployments()[0]

        # No version_registry wired, so entry.version stays "" and
        # never matches either allocation's version — falls back to
        # the largest current allocation.
        assert entry.traffic_percentage == 70.0


# --- Health aggregation --------------------------------------------------


class TestHealthAggregation:

    def test_health_returns_snapshots_from_the_health_engine(self):
        health_engine = DeploymentRolloutHealthEngine(clock=_clock)
        health_engine.evaluate("dep-1")

        dashboard = _dashboard(health_engine=health_engine)

        snapshots = dashboard.health()

        assert len(snapshots) == 1
        assert snapshots[0].deployment_id == "dep-1"

    def test_health_empty_when_not_wired(self):
        dashboard = _dashboard()

        assert dashboard.health() == ()


# --- Analytics aggregation -----------------------------------------------


class TestAnalyticsAggregation:

    def test_analytics_returns_the_current_snapshot(self):
        analytics = DeploymentRolloutAnalytics(clock=_clock)
        analytics.record("dep-1", "SUCCESS", 10.0)

        dashboard = _dashboard(analytics=analytics)

        snapshot = dashboard.analytics()

        assert snapshot is not None
        assert snapshot.successful_rollouts == 1

    def test_analytics_none_when_not_wired(self):
        dashboard = _dashboard()

        assert dashboard.analytics() is None


class TestTrafficAndRollbacksAndPolicies:

    def test_traffic_returns_router_snapshots(self):
        router = DeploymentTrafficRouter(clock=_clock)
        router.configure("dep-1", [("1.0.0", 100.0)])

        dashboard = _dashboard(traffic_router=router)

        assert len(dashboard.traffic()) == 1

    def test_traffic_empty_when_not_wired(self):
        dashboard = _dashboard()

        assert dashboard.traffic() == ()

    def test_rollbacks_returns_plans(self):
        rollback_engine = DeploymentRollbackEngine(clock=_clock)
        rollback_engine.create_plan("dep-1", target_version="1.0.0")

        dashboard = _dashboard(rollback_engine=rollback_engine)

        assert len(dashboard.rollbacks()) == 1

    def test_rollbacks_empty_when_not_wired(self):
        dashboard = _dashboard()

        assert dashboard.rollbacks() == ()

    def test_policies_returns_registered_policies(self):
        policy_engine = DeploymentRolloutPolicyEngine(clock=_clock)
        policy_engine.register("p")

        dashboard = _dashboard(policy_engine=policy_engine)

        assert len(dashboard.policies()) == 1

    def test_policies_empty_when_not_wired(self):
        dashboard = _dashboard()

        assert dashboard.policies() == ()


# --- Dashboard caching -----------------------------------------------


class TestDashboardCaching:

    def test_zero_ttl_never_caches(self):
        clock = _ManualClock()
        rollout_manager = DeploymentRolloutManager(clock=clock)
        dashboard = _dashboard(
            clock=clock, rollout_manager=rollout_manager,
            cache_ttl_seconds=0,
        )

        first = dashboard.overview()

        rollout_manager.create("dep-1", "CANARY")
        clock.advance(1)

        second = dashboard.overview()

        assert first.deployments != second.deployments

    def test_nonzero_ttl_serves_cached_copy(self):
        clock = _ManualClock()
        rollout_manager = DeploymentRolloutManager(clock=clock)
        dashboard = _dashboard(
            clock=clock, rollout_manager=rollout_manager,
            cache_ttl_seconds=60,
        )

        first = dashboard.overview()

        rollout_manager.create("dep-1", "CANARY")
        clock.advance(1)

        second = dashboard.overview()

        assert first == second

    def test_cache_expires_after_ttl(self):
        clock = _ManualClock()
        rollout_manager = DeploymentRolloutManager(clock=clock)
        dashboard = _dashboard(
            clock=clock, rollout_manager=rollout_manager,
            cache_ttl_seconds=10,
        )

        dashboard.overview()

        rollout_manager.create("dep-1", "CANARY")
        clock.advance(11)

        refreshed = dashboard.overview()

        assert len(refreshed.deployments) == 1

    def test_refresh_bypasses_and_replaces_the_cache(self):
        clock = _ManualClock()
        rollout_manager = DeploymentRolloutManager(clock=clock)
        dashboard = _dashboard(
            clock=clock, rollout_manager=rollout_manager,
            cache_ttl_seconds=60,
        )

        dashboard.overview()

        rollout_manager.create("dep-1", "CANARY")
        clock.advance(1)

        refreshed = dashboard.refresh()

        assert len(refreshed.deployments) == 1

        # A subsequent overview() within the TTL now serves the
        # refreshed copy, not the original stale cache.
        assert dashboard.overview() == refreshed

    def test_refresh_publishes_rollout_dashboard_refreshed(self):
        bus = GovernanceEventBus(clock=_clock)
        dashboard = _dashboard(event_bus=bus)

        events = []
        bus.subscribe("rollout_dashboard_refreshed", events.append)

        dashboard.refresh()

        assert len(events) == 1

    def test_negative_ttl_rejected(self):
        with pytest.raises(
            ValueError, match="cache_ttl_seconds must not be negative"
        ):
            _dashboard(cache_ttl_seconds=-1)

    def test_section_accessors_are_never_cached(self):
        clock = _ManualClock()
        rollout_manager = DeploymentRolloutManager(clock=clock)
        dashboard = _dashboard(
            clock=clock, rollout_manager=rollout_manager,
            cache_ttl_seconds=60,
        )

        dashboard.overview()

        rollout_manager.create("dep-1", "CANARY")

        assert len(dashboard.deployments()) == 1


# --- Subsystem failure resilience ---------------------------------------


class TestSubsystemFailureResilience:

    def test_missing_deployment_from_registry_is_skipped_gracefully(self):
        registry = DeploymentVersionRegistry(clock=_clock)
        registry.register("dep-1", "1.0.0", "a.tar.gz", VALID_CHECKSUM)
        registry.remove("dep-1")

        rollout_manager = DeploymentRolloutManager(clock=_clock)
        rollout_manager.create("dep-1", "CANARY")

        dashboard = _dashboard(
            rollout_manager=rollout_manager, version_registry=registry,
        )

        entry = dashboard.deployments()[0]

        assert entry.version == ""

    def test_missing_traffic_snapshot_is_skipped_gracefully(self):
        rollout_manager = DeploymentRolloutManager(clock=_clock)
        rollout_manager.create("dep-1", "CANARY")

        router = DeploymentTrafficRouter(clock=_clock)

        dashboard = _dashboard(
            rollout_manager=rollout_manager, traffic_router=router,
        )

        entry = dashboard.deployments()[0]

        assert entry.traffic_percentage == 0.0

    def test_missing_health_snapshot_is_skipped_gracefully(self):
        rollout_manager = DeploymentRolloutManager(clock=_clock)
        rollout_manager.create("dep-1", "CANARY")

        health_engine = DeploymentRolloutHealthEngine(clock=_clock)

        dashboard = _dashboard(
            rollout_manager=rollout_manager, health_engine=health_engine,
        )

        entry = dashboard.deployments()[0]

        assert entry.health == "UNKNOWN"

    def test_no_subsystems_wired_at_all(self):
        dashboard = _dashboard()

        assert dashboard.overview().deployments == ()
        assert dashboard.health() == ()
        assert dashboard.analytics() is None
        assert dashboard.traffic() == ()
        assert dashboard.rollbacks() == ()
        assert dashboard.policies() == ()

    def test_blue_green_deployment_does_not_break_aggregation(self):
        """
        Not a wired dependency of this dashboard, but exercising it
        alongside the others confirms nothing about this aggregation
        assumes only one strategy engine's shape.
        """

        blue_green = BlueGreenDeploymentEngine(clock=_clock)
        blue_green.deploy("dep-1", "1.1.0", blue_version="1.0.0")

        rollout_manager = DeploymentRolloutManager(clock=_clock)
        rollout_manager.create("dep-1", "BLUE_GREEN")

        dashboard = _dashboard(rollout_manager=rollout_manager)

        entries = dashboard.deployments()

        assert entries[0].strategy == "BLUE_GREEN"


# --- Deterministic ordering ---------------------------------------------


class TestDeterministicOrdering:

    def test_deployments_ordered_by_deployment_id(self):
        rollout_manager = DeploymentRolloutManager(clock=_clock)
        rollout_manager.create("dep-b", "CANARY")
        rollout_manager.create("dep-a", "CANARY")

        dashboard = _dashboard(rollout_manager=rollout_manager)

        entries = dashboard.deployments()

        assert [e.deployment_id for e in entries] == ["dep-a", "dep-b"]


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_rollout_dashboard_returns_same_instance(self):
        assert get_rollout_dashboard() is get_rollout_dashboard()

    def test_singleton_is_wired_to_every_subsystem(self):
        dashboard = get_rollout_dashboard()

        assert dashboard._rollout_manager is not None
        assert dashboard._version_registry is not None
        assert dashboard._traffic_router is not None
        assert dashboard._health_engine is not None
        assert dashboard._rollback_engine is not None
        assert dashboard._analytics is not None
        assert dashboard._policy_engine is not None


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceRolloutDashboardApi:

    def test_get_dashboard(self, client):
        response = client.get("/governance/rollouts/dashboard")

        assert response.status_code == 200
        assert "active_rollouts" in response.json()

    def test_get_deployments(self, client):
        response = client.get(
            "/governance/rollouts/dashboard/deployments"
        )

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_traffic(self, client):
        response = client.get("/governance/rollouts/dashboard/traffic")

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_health(self, client):
        response = client.get("/governance/rollouts/dashboard/health")

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_analytics(self, client):
        response = client.get(
            "/governance/rollouts/dashboard/analytics"
        )

        assert response.status_code == 200
        assert "successful_rollouts" in response.json()

    def test_get_rollbacks(self, client):
        response = client.get(
            "/governance/rollouts/dashboard/rollbacks"
        )

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_deployment_appears_in_dashboard_after_creation(self, client):
        client.post(
            "/governance/deployments",
            params={
                "deployment_id": "dep-api-dashboard",
                "version": "1.0.0",
                "artifact": "a.tar.gz",
                "checksum": VALID_CHECKSUM,
            },
        )

        response = client.get(
            "/governance/rollouts/dashboard/deployments"
        )

        deployment_ids = {
            entry["deployment_id"] for entry in response.json()
        }

        assert "dep-api-dashboard" in deployment_ids
