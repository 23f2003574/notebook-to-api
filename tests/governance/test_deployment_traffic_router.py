from __future__ import annotations

import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_scheduler_metrics import (
    GovernanceSchedulerMetrics,
)
from backend.observability.deployment_governance_traffic_router import (
    DeploymentTrafficRouter,
    RoutingSnapshot,
    TrafficAllocation,
    get_traffic_router,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _router(**kwargs) -> DeploymentTrafficRouter:
    return DeploymentTrafficRouter(clock=_clock, **kwargs)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The traffic router is a process-wide singleton; most tests below
    construct their own fresh router instead (see _router), and only
    the singleton and API tests touch the shared instance, matching
    test_deployment_canary.py's own fixture.
    """

    def _reset():
        get_traffic_router().clear()

    _reset()
    yield
    _reset()


# --- Models ------------------------------------------------------------


class TestTrafficAllocation:

    def test_rejects_empty_deployment_id(self):
        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            TrafficAllocation(
                deployment_id="", version="1.0.0", percentage=100.0
            )

    def test_rejects_empty_version(self):
        with pytest.raises(ValueError, match="version must not be empty"):
            TrafficAllocation(
                deployment_id="dep-1", version="", percentage=100.0
            )

    def test_rejects_negative_percentage(self):
        with pytest.raises(
            ValueError, match="percentage must not be negative"
        ):
            TrafficAllocation(
                deployment_id="dep-1", version="1.0.0", percentage=-1.0
            )

    def test_to_dict(self):
        allocation = TrafficAllocation(
            deployment_id="dep-1", version="1.0.0", percentage=50.0
        )

        assert allocation.to_dict() == {
            "deployment_id": "dep-1",
            "version": "1.0.0",
            "percentage": 50.0,
        }


class TestRoutingSnapshot:

    def test_rejects_empty_deployment_id(self):
        with pytest.raises(
            ValueError, match="deployment_id must not be empty"
        ):
            RoutingSnapshot(
                deployment_id="", allocations=(), updated_at=BASE_TIME
            )

    def test_rejects_naive_updated_at(self):
        with pytest.raises(
            ValueError, match="updated_at must be timezone-aware"
        ):
            RoutingSnapshot(
                deployment_id="dep-1", allocations=(),
                updated_at=datetime(2026, 7, 23, 12, 0, 0),
            )

    def test_empty_allocations_is_constructible(self):
        snapshot = RoutingSnapshot(
            deployment_id="dep-1", allocations=(), updated_at=BASE_TIME,
        )

        assert snapshot.allocations == ()

    def test_to_dict(self):
        snapshot = RoutingSnapshot(
            deployment_id="dep-1",
            allocations=(
                TrafficAllocation(
                    deployment_id="dep-1", version="1.0.0",
                    percentage=100.0,
                ),
            ),
            updated_at=BASE_TIME,
        )

        assert snapshot.to_dict() == {
            "deployment_id": "dep-1",
            "allocations": [
                {
                    "deployment_id": "dep-1", "version": "1.0.0",
                    "percentage": 100.0,
                },
            ],
            "updated_at": BASE_TIME.isoformat(),
        }


# --- Routing configuration -------------------------------------------


class TestConfigure:

    def test_configure_stores_a_snapshot(self):
        router = _router()

        snapshot = router.configure(
            "dep-1", [("1.0.0", 100.0)], strategy="STATIC"
        )

        assert snapshot.deployment_id == "dep-1"
        assert len(snapshot.allocations) == 1
        assert snapshot.allocations[0].percentage == 100.0

    def test_configure_rejects_unknown_strategy(self):
        router = _router()

        with pytest.raises(ValueError, match="is not registered"):
            router.configure("dep-1", [("1.0.0", 100.0)], strategy="BOGUS")

    def test_configure_can_be_called_repeatedly(self):
        router = _router()
        router.configure("dep-1", [("1.0.0", 100.0)])

        snapshot = router.configure("dep-1", [("2.0.0", 100.0)])

        assert snapshot.allocations[0].version == "2.0.0"

    def test_configure_orders_allocations_by_version(self):
        router = _router()

        snapshot = router.configure(
            "dep-1", [("2.0.0", 50.0), ("1.0.0", 50.0)],
        )

        assert [a.version for a in snapshot.allocations] == [
            "1.0.0", "2.0.0",
        ]

    def test_configure_publishes_routing_configured(self):
        bus = GovernanceEventBus(clock=_clock)
        router = _router(event_bus=bus)

        events = []
        bus.subscribe("routing_configured", events.append)

        router.configure("dep-1", [("1.0.0", 100.0)])

        assert len(events) == 1
        assert events[0].source == "dep-1"


class TestUpdate:

    def test_update_replaces_the_current_table(self):
        router = _router()
        router.configure("dep-1", [("1.0.0", 100.0)])

        snapshot = router.update("dep-1", [("2.0.0", 100.0)])

        assert snapshot.allocations[0].version == "2.0.0"

    def test_update_unconfigured_deployment_raises_key_error(self):
        router = _router()

        with pytest.raises(KeyError):
            router.update("dep-1", [("1.0.0", 100.0)])

    def test_update_publishes_routing_updated(self):
        bus = GovernanceEventBus(clock=_clock)
        router = _router(event_bus=bus)
        router.configure("dep-1", [("1.0.0", 100.0)])

        events = []
        bus.subscribe("routing_updated", events.append)

        router.update("dep-1", [("2.0.0", 100.0)])

        assert len(events) == 1


# --- Allocation validation -------------------------------------------


class TestValidate:

    def test_validate_true_for_a_valid_table(self):
        router = _router()

        assert router.validate(
            [
                TrafficAllocation("dep-1", "1.0.0", 60.0),
                TrafficAllocation("dep-1", "2.0.0", 40.0),
            ]
        ) is True

    def test_validate_false_for_empty(self):
        router = _router()

        assert router.validate([]) is False

    def test_validate_false_when_total_is_not_100(self):
        router = _router()

        assert router.validate(
            [TrafficAllocation("dep-1", "1.0.0", 50.0)]
        ) is False

    def test_validate_false_with_negative_allocation(self):
        # TrafficAllocation itself already refuses a negative
        # percentage at construction, so validate()'s own negative
        # check can only ever see one via a duck-typed stand-in (a
        # defense-in-depth check for the interface, not TrafficAllocation
        # objects specifically).
        router = _router()

        allocations = [
            SimpleNamespace(percentage=150.0),
            SimpleNamespace(percentage=-50.0),
        ]

        assert router.validate(allocations) is False

    def test_configure_rejects_invalid_total(self):
        router = _router()

        with pytest.raises(
            ValueError, match="must be non-negative and total 100%"
        ):
            router.configure("dep-1", [("1.0.0", 50.0)])

    def test_configure_publishes_routing_validation_failed(self):
        bus = GovernanceEventBus(clock=_clock)
        router = _router(event_bus=bus)

        events = []
        bus.subscribe("routing_validation_failed", events.append)

        with pytest.raises(ValueError):
            router.configure("dep-1", [("1.0.0", 50.0)])

        assert len(events) == 1

    def test_invalid_configure_does_not_store_anything(self):
        router = _router()

        with pytest.raises(ValueError):
            router.configure("dep-1", [("1.0.0", 50.0)])

        with pytest.raises(KeyError):
            router.snapshot("dep-1")


# --- Weighted routing -----------------------------------------------


class TestWeightedRouting:

    def test_configure_with_weighted_strategy(self):
        router = _router()

        snapshot = router.configure(
            "dep-1",
            [("1.0.0", 33.0), ("1.1.0", 33.0), ("1.2.0", 34.0)],
            strategy="WEIGHTED",
        )

        assert len(snapshot.allocations) == 3

    def test_weighted_rebalance_splits_evenly(self):
        router = _router()
        router.configure(
            "dep-1",
            [("1.0.0", 90.0), ("1.1.0", 10.0)],
            strategy="WEIGHTED",
        )

        snapshot = router.rebalance("dep-1")

        percentages = sorted(a.percentage for a in snapshot.allocations)

        assert percentages == pytest.approx([50.0, 50.0])

    def test_custom_strategy_via_register_strategy(self):
        router = _router()

        def _favor_first(allocations):
            if not allocations:
                return allocations

            first, *rest = allocations
            return (
                TrafficAllocation(
                    first.deployment_id, first.version, 80.0
                ),
                *(
                    TrafficAllocation(
                        a.deployment_id, a.version,
                        20.0 / len(rest) if rest else 0.0,
                    )
                    for a in rest
                ),
            )

        router.register_strategy("FAVOR_FIRST", _favor_first)
        router.configure(
            "dep-1", [("1.0.0", 50.0), ("1.1.0", 50.0)],
            strategy="FAVOR_FIRST",
        )

        snapshot = router.rebalance("dep-1")

        allocation_by_version = {
            a.version: a.percentage for a in snapshot.allocations
        }

        assert allocation_by_version["1.0.0"] == 80.0
        assert allocation_by_version["1.1.0"] == 20.0


# --- Canary routing ----------------------------------------------------


class TestCanaryRouting:

    def test_allocate_shifts_traffic_to_the_named_version(self):
        router = _router()
        router.configure(
            "dep-1", [("1.0.0", 100.0), ("1.1.0", 0.0)], strategy="CANARY",
        )

        snapshot = router.allocate("dep-1", "1.1.0", 25.0)

        allocation_by_version = {
            a.version: a.percentage for a in snapshot.allocations
        }

        assert allocation_by_version["1.1.0"] == 25.0
        assert allocation_by_version["1.0.0"] == pytest.approx(75.0)

    def test_allocate_unconfigured_deployment_raises_key_error(self):
        router = _router()

        with pytest.raises(KeyError):
            router.allocate("dep-1", "1.1.0", 25.0)

    def test_allocate_rejects_out_of_range_percentage(self):
        router = _router()
        router.configure("dep-1", [("1.0.0", 100.0)])

        with pytest.raises(
            ValueError, match="percentage must be between 0 and 100"
        ):
            router.allocate("dep-1", "1.0.0", 150.0)

    def test_full_canary_progression(self):
        router = _router()
        router.configure(
            "dep-1", [("1.0.0", 100.0), ("1.1.0", 0.0)], strategy="CANARY",
        )

        for pct in (5.0, 25.0, 50.0, 100.0):
            router.allocate("dep-1", "1.1.0", pct)

        snapshot = router.snapshot("dep-1")
        allocation_by_version = {
            a.version: a.percentage for a in snapshot.allocations
        }

        assert allocation_by_version["1.1.0"] == 100.0
        assert allocation_by_version["1.0.0"] == 0.0


# --- Blue/Green routing --------------------------------------------------


class TestBlueGreenRouting:

    def test_configure_starts_all_traffic_on_blue(self):
        router = _router()

        snapshot = router.configure(
            "dep-1", [("1.0.0", 100.0), ("1.1.0", 0.0)],
            strategy="BLUE_GREEN",
        )

        allocation_by_version = {
            a.version: a.percentage for a in snapshot.allocations
        }

        assert allocation_by_version["1.0.0"] == 100.0
        assert allocation_by_version["1.1.0"] == 0.0

    def test_allocate_performs_an_atomic_full_switch(self):
        router = _router()
        router.configure(
            "dep-1", [("1.0.0", 100.0), ("1.1.0", 0.0)],
            strategy="BLUE_GREEN",
        )

        snapshot = router.allocate("dep-1", "1.1.0", 100.0)

        allocation_by_version = {
            a.version: a.percentage for a in snapshot.allocations
        }

        assert allocation_by_version["1.1.0"] == 100.0
        assert allocation_by_version["1.0.0"] == 0.0


# --- Rebalancing ---------------------------------------------------------


class TestRebalance:

    def test_static_rebalance_leaves_allocations_unchanged(self):
        router = _router()
        router.configure(
            "dep-1", [("1.0.0", 70.0), ("1.1.0", 30.0)], strategy="STATIC",
        )

        snapshot = router.rebalance("dep-1")

        allocation_by_version = {
            a.version: a.percentage for a in snapshot.allocations
        }

        assert allocation_by_version["1.0.0"] == 70.0
        assert allocation_by_version["1.1.0"] == 30.0

    def test_rebalance_unconfigured_deployment_raises_key_error(self):
        router = _router()

        with pytest.raises(KeyError):
            router.rebalance("dep-1")

    def test_rebalance_publishes_routing_rebalanced(self):
        bus = GovernanceEventBus(clock=_clock)
        router = _router(event_bus=bus)
        router.configure(
            "dep-1", [("1.0.0", 70.0), ("1.1.0", 30.0)], strategy="WEIGHTED",
        )

        events = []
        bus.subscribe("routing_rebalanced", events.append)

        router.rebalance("dep-1")

        assert len(events) == 1

    def test_rebalance_appends_to_history(self):
        router = _router()
        router.configure(
            "dep-1", [("1.0.0", 70.0), ("1.1.0", 30.0)], strategy="WEIGHTED",
        )

        router.rebalance("dep-1")

        assert len(router.history("dep-1")) == 2


# --- Snapshot generation -----------------------------------------------


class TestSnapshot:

    def test_snapshot_unconfigured_deployment_raises_key_error(self):
        router = _router()

        with pytest.raises(KeyError):
            router.snapshot("dep-1")

    def test_snapshot_is_immutable(self):
        router = _router()
        first = router.configure("dep-1", [("1.0.0", 100.0)])
        router.configure("dep-1", [("1.1.0", 100.0)])

        assert first.allocations[0].version == "1.0.0"
        assert router.snapshot("dep-1").allocations[0].version == "1.1.0"


class TestHistory:

    def test_history_empty_for_unconfigured_deployment(self):
        router = _router()

        assert router.history("dep-1") == ()

    def test_history_is_append_only(self):
        router = _router()
        router.configure("dep-1", [("1.0.0", 100.0)])
        router.update("dep-1", [("1.1.0", 100.0)])
        router.allocate("dep-1", "1.1.0", 100.0)

        assert len(router.history("dep-1")) == 3


class TestReset:

    def test_reset_clears_the_routing_table(self):
        router = _router()
        router.configure("dep-1", [("1.0.0", 100.0)])

        snapshot = router.reset("dep-1")

        assert snapshot.allocations == ()
        assert router.snapshot("dep-1").allocations == ()

    def test_reset_works_on_a_never_configured_deployment(self):
        router = _router()

        snapshot = router.reset("dep-1")

        assert snapshot.allocations == ()

    def test_reset_publishes_routing_reset(self):
        bus = GovernanceEventBus(clock=_clock)
        router = _router(event_bus=bus)

        events = []
        bus.subscribe("routing_reset", events.append)

        router.reset("dep-1")

        assert len(events) == 1

    def test_configure_after_reset_works(self):
        router = _router()
        router.configure("dep-1", [("1.0.0", 100.0)])
        router.reset("dep-1")

        snapshot = router.configure("dep-1", [("2.0.0", 100.0)])

        assert snapshot.allocations[0].version == "2.0.0"


class TestList:

    def test_list_orders_by_deployment_id(self):
        router = _router()
        router.configure("dep-b", [("1.0.0", 100.0)])
        router.configure("dep-a", [("1.0.0", 100.0)])

        listed = router.list()

        assert [s.deployment_id for s in listed] == ["dep-a", "dep-b"]

    def test_list_empty_when_nothing_configured(self):
        router = _router()

        assert router.list() == ()


# --- Concurrent updates -----------------------------------------------


class TestConcurrentUpdates:

    def test_concurrent_allocate_calls_never_lose_an_update(self):
        """
        allocate() reads the current snapshot and writes a recomputed
        one; if that read-then-write were not atomic (see the
        _recompute fix that keeps it inside a single lock
        acquisition), concurrent callers could clobber each other's
        writes. This drives many concurrent allocate() calls and
        checks every one of them is reflected in history — none
        silently lost — and that the final table is still valid.
        """

        router = _router()
        router.configure(
            "dep-1", [("1.0.0", 100.0), ("1.1.0", 0.0)], strategy="CANARY",
        )

        thread_count = 20
        barrier = threading.Barrier(thread_count)

        def _worker(i: int) -> None:
            barrier.wait()
            router.allocate("dep-1", "1.1.0", float(i % 100))

        threads = [
            threading.Thread(target=_worker, args=(i,))
            for i in range(thread_count)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # +1 for the initial configure().
        assert len(router.history("dep-1")) == thread_count + 1

        final = router.snapshot("dep-1")

        assert router.validate(final.allocations)

    def test_concurrent_configure_calls_all_succeed(self):
        router = _router()

        def _worker(i: int) -> None:
            router.configure(f"dep-{i}", [("1.0.0", 100.0)])

        threads = [
            threading.Thread(target=_worker, args=(i,))
            for i in range(20)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert len(router.list()) == 20


# --- Event publication -----------------------------------------------


class TestEventPublication:

    def test_no_event_bus_is_safe(self):
        router = _router(event_bus=None)

        router.configure("dep-1", [("1.0.0", 100.0), ("1.1.0", 0.0)])
        router.update("dep-1", [("1.0.0", 100.0), ("1.1.0", 0.0)])
        router.allocate("dep-1", "1.1.0", 50.0)
        router.rebalance("dep-1")
        router.reset("dep-1")

        with pytest.raises(ValueError):
            router.configure("dep-1", [("1.0.0", 50.0)])


class TestMetricsIntegration:

    def test_successful_write_records_a_policy_decision(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        router = _router(metrics=metrics)

        router.configure("dep-1", [("1.0.0", 100.0)])

        assert metrics.policy_decisions["allowed"] == 1

    def test_failed_write_records_a_denied_policy_decision(self):
        metrics = GovernanceSchedulerMetrics(clock=_clock)
        router = _router(metrics=metrics)

        with pytest.raises(ValueError):
            router.configure("dep-1", [("1.0.0", 50.0)])

        assert metrics.policy_decisions["denied"] == 1

    def test_no_metrics_wired_is_safe(self):
        router = _router(metrics=None)

        router.configure("dep-1", [("1.0.0", 100.0)])


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_traffic_router_returns_same_instance(self):
        assert get_traffic_router() is get_traffic_router()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceRoutingApi:

    def test_post_configures_routing(self, client):
        response = client.post(
            "/governance/routing/dep-api-1",
            params={
                "versions": ["1.0.0"], "percentages": [100.0],
                "strategy": "STATIC",
            },
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["allocations"][0]["percentage"] == 100.0

    def test_post_invalid_total_returns_409(self, client):
        response = client.post(
            "/governance/routing/dep-api-2",
            params={"versions": ["1.0.0"], "percentages": [50.0]},
        )

        assert response.status_code == 409

    def test_post_mismatched_lengths_returns_422(self, client):
        response = client.post(
            "/governance/routing/dep-api-3",
            params={
                "versions": ["1.0.0", "1.1.0"], "percentages": [100.0],
            },
        )

        assert response.status_code == 422

    def test_get_snapshot(self, client):
        client.post(
            "/governance/routing/dep-api-4",
            params={"versions": ["1.0.0"], "percentages": [100.0]},
        )

        response = client.get("/governance/routing/dep-api-4")

        assert response.status_code == 200
        assert response.json()["deployment_id"] == "dep-api-4"

    def test_get_unknown_deployment_returns_404(self, client):
        response = client.get("/governance/routing/does-not-exist")

        assert response.status_code == 404

    def test_list_snapshots(self, client):
        client.post(
            "/governance/routing/dep-api-5",
            params={"versions": ["1.0.0"], "percentages": [100.0]},
        )

        response = client.get("/governance/routing")

        assert response.status_code == 200
        assert any(
            s["deployment_id"] == "dep-api-5" for s in response.json()
        )

    def test_patch_updates_routing(self, client):
        client.post(
            "/governance/routing/dep-api-6",
            params={"versions": ["1.0.0"], "percentages": [100.0]},
        )

        response = client.patch(
            "/governance/routing/dep-api-6",
            params={"versions": ["2.0.0"], "percentages": [100.0]},
        )

        assert response.status_code == 200
        assert response.json()["allocations"][0]["version"] == "2.0.0"

    def test_patch_unconfigured_deployment_returns_404(self, client):
        response = client.patch(
            "/governance/routing/does-not-exist",
            params={"versions": ["1.0.0"], "percentages": [100.0]},
        )

        assert response.status_code == 404

    def test_rebalance(self, client):
        client.post(
            "/governance/routing/dep-api-7",
            params={
                "versions": ["1.0.0", "1.1.0"],
                "percentages": [90.0, 10.0],
                "strategy": "WEIGHTED",
            },
        )

        response = client.post(
            "/governance/routing/dep-api-7/rebalance"
        )

        assert response.status_code == 200

        percentages = sorted(
            a["percentage"] for a in response.json()["allocations"]
        )

        assert percentages == pytest.approx([50.0, 50.0])

    def test_rebalance_unconfigured_deployment_returns_404(self, client):
        response = client.post(
            "/governance/routing/does-not-exist/rebalance"
        )

        assert response.status_code == 404

    def test_reset(self, client):
        client.post(
            "/governance/routing/dep-api-8",
            params={"versions": ["1.0.0"], "percentages": [100.0]},
        )

        response = client.post("/governance/routing/dep-api-8/reset")

        assert response.status_code == 200
        assert response.json()["allocations"] == []
