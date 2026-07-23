from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GOVERNANCE_EVENT_TYPES,
    EventSubscription,
    GovernanceEvent,
    GovernanceEventBus,
    get_event_bus,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singleton_bus():
    """
    The event bus is a process-wide singleton, so tests that touch
    get_event_bus() (directly or via the API endpoints) must not leak
    subscriptions into other tests.
    """

    get_event_bus().clear()

    yield

    get_event_bus().clear()


# --- Model -------------------------------------------------------------


class TestGovernanceEvent:

    def test_rejects_empty_event_id(self):
        with pytest.raises(ValueError, match="event_id must not be empty"):
            GovernanceEvent(
                event_id="",
                event_type="component_started",
                source="a",
                payload={},
                occurred_at=BASE_TIME,
            )

    def test_rejects_empty_event_type(self):
        with pytest.raises(
            ValueError, match="event_type must not be empty"
        ):
            GovernanceEvent(
                event_id="1",
                event_type="",
                source="a",
                payload={},
                occurred_at=BASE_TIME,
            )

    def test_rejects_empty_source(self):
        with pytest.raises(ValueError, match="source must not be empty"):
            GovernanceEvent(
                event_id="1",
                event_type="component_started",
                source="",
                payload={},
                occurred_at=BASE_TIME,
            )

    def test_rejects_naive_occurred_at(self):
        with pytest.raises(
            ValueError, match="occurred_at must be timezone-aware"
        ):
            GovernanceEvent(
                event_id="1",
                event_type="component_started",
                source="a",
                payload={},
                occurred_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_payload_is_immutable(self):
        event = GovernanceEvent(
            event_id="1",
            event_type="component_started",
            source="a",
            payload={"x": 1},
            occurred_at=BASE_TIME,
        )

        with pytest.raises(TypeError):
            event.payload["x"] = 2

    def test_mutating_the_original_payload_does_not_affect_the_event(
        self,
    ):
        payload = {"x": 1}

        event = GovernanceEvent(
            event_id="1",
            event_type="component_started",
            source="a",
            payload=payload,
            occurred_at=BASE_TIME,
        )

        payload["x"] = 999

        assert event.payload["x"] == 1

    def test_to_dict(self):
        event = GovernanceEvent(
            event_id="1",
            event_type="component_started",
            source="a",
            payload={"x": 1},
            occurred_at=BASE_TIME,
        )

        assert event.to_dict() == {
            "event_id": "1",
            "event_type": "component_started",
            "source": "a",
            "payload": {"x": 1},
            "occurred_at": BASE_TIME.isoformat(),
        }


def test_event_subscription_to_dict():
    def handler(event):
        pass

    subscription = EventSubscription(
        event_type="component_started", handler=handler
    )

    assert subscription.to_dict() == {
        "event_type": "component_started",
        "handler": "handler",
    }


# --- Subscribe -----------------------------------------------------------


class TestGovernanceEventBusSubscribe:

    def test_subscribe_returns_subscription(self):
        bus = GovernanceEventBus()

        subscription = bus.subscribe("component_started", lambda e: None)

        assert subscription.event_type == "component_started"

    def test_subscribe_rejects_empty_event_type(self):
        bus = GovernanceEventBus()

        with pytest.raises(ValueError, match="event_type must not be empty"):
            bus.subscribe("", lambda e: None)

    def test_subscribers_reflects_subscription(self):
        bus = GovernanceEventBus()
        subscription = bus.subscribe("component_started", lambda e: None)

        assert bus.subscribers("component_started") == (subscription,)


# --- Unsubscribe -----------------------------------------------------------


class TestGovernanceEventBusUnsubscribe:

    def test_unsubscribe_removes_subscription(self):
        bus = GovernanceEventBus()
        subscription = bus.subscribe("component_started", lambda e: None)

        bus.unsubscribe(subscription)

        assert bus.subscribers("component_started") == ()

    def test_unsubscribed_handler_is_not_called(self):
        calls = []

        bus = GovernanceEventBus()
        subscription = bus.subscribe(
            "component_started", lambda e: calls.append(e)
        )

        bus.unsubscribe(subscription)
        bus.publish("component_started", source="a")

        assert calls == []

    def test_unsubscribe_unknown_subscription_raises(self):
        bus = GovernanceEventBus()

        with pytest.raises(ValueError, match="not registered"):
            bus.unsubscribe(
                EventSubscription(
                    event_type="component_started", handler=lambda e: None
                )
            )


# --- Multiple subscribers -------------------------------------------------


class TestGovernanceEventBusMultipleSubscribers:

    def test_every_subscriber_is_called(self):
        calls = []

        bus = GovernanceEventBus()
        bus.subscribe("component_started", lambda e: calls.append("a"))
        bus.subscribe("component_started", lambda e: calls.append("b"))

        bus.publish("component_started", source="x")

        assert calls == ["a", "b"]

    def test_subscribers_for_different_event_types_are_independent(self):
        calls = []

        bus = GovernanceEventBus()
        bus.subscribe("component_started", lambda e: calls.append("started"))
        bus.subscribe("component_stopped", lambda e: calls.append("stopped"))

        bus.publish("component_started", source="x")

        assert calls == ["started"]


# --- Dispatch ordering -----------------------------------------------------


class TestGovernanceEventBusDispatchOrdering:

    def test_handlers_dispatched_in_subscription_order(self):
        calls = []

        bus = GovernanceEventBus()
        bus.subscribe("component_started", lambda e: calls.append(1))
        bus.subscribe("component_started", lambda e: calls.append(2))
        bus.subscribe("component_started", lambda e: calls.append(3))

        bus.publish("component_started", source="x")

        assert calls == [1, 2, 3]

    def test_subscribers_all_returns_types_sorted_then_registration_order(
        self,
    ):
        bus = GovernanceEventBus()
        bus.subscribe("component_stopped", lambda e: None)
        bus.subscribe("component_started", lambda e: None)

        assert [s.event_type for s in bus.subscribers()] == [
            "component_started",
            "component_stopped",
        ]


# --- Handler isolation -----------------------------------------------------


class TestGovernanceEventBusHandlerIsolation:

    def test_raising_handler_does_not_stop_other_handlers(self):
        calls = []

        def _boom(event):
            raise RuntimeError("boom")

        bus = GovernanceEventBus()
        bus.subscribe("component_started", _boom)
        bus.subscribe("component_started", lambda e: calls.append("ok"))

        bus.publish("component_started", source="x")

        assert calls == ["ok"]

    def test_raising_handler_does_not_propagate_to_publisher(self):
        def _boom(event):
            raise RuntimeError("boom")

        bus = GovernanceEventBus()
        bus.subscribe("component_started", _boom)

        event = bus.publish("component_started", source="x")

        assert event.event_type == "component_started"


# --- Batch publishing --------------------------------------------------


class TestGovernanceEventBusBatchPublishing:

    def test_publish_batch_publishes_every_event_in_order(self):
        calls = []

        bus = GovernanceEventBus(clock=_clock)
        bus.subscribe("component_started", lambda e: calls.append(e.source))

        events = bus.publish_batch(
            [
                ("component_started", "a", None),
                ("component_started", "b", {"x": 1}),
            ]
        )

        assert calls == ["a", "b"]
        assert [e.source for e in events] == ["a", "b"]

    def test_publish_batch_returns_immutable_events_with_uuid_ids(self):
        bus = GovernanceEventBus()

        events = bus.publish_batch(
            [("component_started", "a", None)]
        )

        assert len(events[0].event_id) > 0


# --- Event metadata --------------------------------------------------------


class TestGovernanceEventBusMetadata:

    def test_publish_assigns_uuid_event_id(self):
        bus = GovernanceEventBus()

        event_1 = bus.publish("component_started", source="a")
        event_2 = bus.publish("component_started", source="a")

        assert event_1.event_id != event_2.event_id

    def test_publish_assigns_utc_timestamp(self):
        bus = GovernanceEventBus(clock=_clock)

        event = bus.publish("component_started", source="a")

        assert event.occurred_at == BASE_TIME

    def test_publish_defaults_payload_to_empty_dict(self):
        bus = GovernanceEventBus()

        event = bus.publish("component_started", source="a")

        assert dict(event.payload) == {}

    def test_publish_carries_payload_through(self):
        bus = GovernanceEventBus()

        event = bus.publish(
            "component_started", source="a", payload={"key": "value"}
        )

        assert dict(event.payload) == {"key": "value"}


def test_clear_removes_every_subscription():
    bus = GovernanceEventBus()
    bus.subscribe("component_started", lambda e: None)
    bus.subscribe("component_stopped", lambda e: None)

    bus.clear()

    assert bus.subscribers() == ()


def test_governance_event_types_is_the_documented_vocabulary():
    assert set(GOVERNANCE_EVENT_TYPES) == {
        "component_started",
        "component_stopped",
        "component_failed",
        "health_check_completed",
        "readiness_check_completed",
        "lifecycle_completed",
        "metrics_snapshot_created",
        "recovery_started",
        "recovery_retry",
        "recovery_succeeded",
        "recovery_failed",
        "recovery_aborted",
        "scheduler_started",
        "scheduler_stopped",
        "job_registered",
        "job_unregistered",
        "job_registry_registered",
        "job_registry_removed",
        "job_enabled",
        "job_disabled",
        "trigger_registered",
        "trigger_removed",
        "trigger_fired",
        "trigger_rescheduled",
        "execution_started",
        "execution_completed",
        "execution_failed",
        "execution_cancelled",
        "retry_scheduled",
        "retry_started",
        "retry_succeeded",
        "retry_exhausted",
        "retry_cancelled",
        "persistence_loaded",
        "persistence_saved",
        "persistence_failed",
        "snapshot_created",
        "cron_registered",
        "cron_removed",
        "cron_triggered",
        "cron_rescheduled",
        "dependency_registered",
        "dependency_removed",
        "dependency_blocked",
        "dependency_resolved",
        "dependency_cycle_detected",
        "lock_acquired",
        "lock_released",
        "lock_renewed",
        "lock_expired",
        "lock_contention",
        "scheduler_metrics_snapshot",
        "scheduler_metrics_reset",
        "scheduler_metrics_threshold_exceeded",
        "scheduler_policy_allowed",
        "scheduler_policy_denied",
        "scheduler_policy_registered",
        "scheduler_policy_removed",
        "scheduler_dashboard_generated",
        "scheduler_dashboard_refreshed",
        "scheduler_bootstrap_started",
        "scheduler_bootstrap_completed",
        "scheduler_bootstrap_failed",
        "scheduler_runtime_ready",
        "scheduler_runtime_shutdown",
        "rollout_created",
        "rollout_started",
        "rollout_paused",
        "rollout_resumed",
        "rollout_completed",
        "rollout_failed",
        "rollout_cancelled",
        "deployment_registered",
        "deployment_updated",
        "deployment_removed",
        "deployment_revision_created",
    }


# --- Runtime integration -------------------------------------------------


class TestHealthServiceEventIntegration:

    def test_summary_publishes_health_check_completed(self):
        from backend.observability.deployment_governance_health import (
            GovernanceHealthService,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe(
            "health_check_completed", lambda e: received.append(e)
        )

        service = GovernanceHealthService(event_bus=bus)
        service.register("a", lambda: True)

        service.summary()

        assert len(received) == 1
        assert received[0].source == "health_service"
        assert received[0].payload["healthy"] is True

    def test_summary_without_event_bus_does_not_raise(self):
        from backend.observability.deployment_governance_health import (
            GovernanceHealthService,
        )

        service = GovernanceHealthService()
        service.register("a", lambda: True)

        service.summary()


class TestMetricsBootstrapEventIntegration:

    def _persistence_runtime(self):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
        )

        return build_deployment_governance_persistence()

    def test_initialize_publishes_metrics_snapshot_created(self):
        from backend.observability.deployment_governance_metrics_bootstrap import (
            GovernanceIntegrityMetricsBootstrap,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe(
            "metrics_snapshot_created", lambda e: received.append(e)
        )

        bootstrap = GovernanceIntegrityMetricsBootstrap(
            self._persistence_runtime(), event_bus=bus
        ).build()

        bootstrap.initialize()

        try:
            assert len(received) == 1
            assert received[0].source == "metrics_bootstrap"
            assert "total_dispatches" in received[0].payload

        finally:
            bootstrap.shutdown()


class TestLifecycleManagerEventIntegration:

    def test_startup_publishes_component_started_and_lifecycle_completed(
        self,
    ):
        from backend.observability.deployment_governance_lifecycle import (
            GovernanceLifecycleManager,
        )

        bus = GovernanceEventBus()
        started_events = []
        lifecycle_events = []
        bus.subscribe(
            "component_started", lambda e: started_events.append(e)
        )
        bus.subscribe(
            "lifecycle_completed", lambda e: lifecycle_events.append(e)
        )

        manager = GovernanceLifecycleManager(event_bus=bus)
        manager.register("a", start=lambda: None, stop=lambda: None)

        manager.startup()

        assert [e.source for e in started_events] == ["a"]
        assert len(lifecycle_events) == 1
        assert lifecycle_events[0].payload["started"] == ["a"]

    def test_shutdown_publishes_component_stopped(self):
        from backend.observability.deployment_governance_lifecycle import (
            GovernanceLifecycleManager,
        )

        bus = GovernanceEventBus()
        stopped_events = []
        bus.subscribe(
            "component_stopped", lambda e: stopped_events.append(e)
        )

        manager = GovernanceLifecycleManager(event_bus=bus)
        manager.register("a", start=lambda: None, stop=lambda: None)

        manager.startup()
        manager.shutdown()

        assert [e.source for e in stopped_events] == ["a"]

    def test_failed_start_publishes_component_failed(self):
        from backend.observability.deployment_governance_lifecycle import (
            GovernanceLifecycleManager,
        )

        bus = GovernanceEventBus()
        failed_events = []
        bus.subscribe(
            "component_failed", lambda e: failed_events.append(e)
        )

        manager = GovernanceLifecycleManager(event_bus=bus)
        manager.register(
            "a",
            start=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            stop=lambda: None,
        )

        manager.startup()

        assert [e.source for e in failed_events] == ["a"]
        assert failed_events[0].payload["phase"] == "startup"

    def test_singleton_manager_publishes_to_singleton_bus(self):
        from backend.observability.deployment_governance_lifecycle import (
            get_lifecycle_manager,
        )

        received = []
        subscription = get_event_bus().subscribe(
            "lifecycle_completed", lambda e: received.append(e)
        )

        try:
            manager = get_lifecycle_manager()
            manager.startup()

            assert len(received) == 1

        finally:
            manager.shutdown()
            get_event_bus().unsubscribe(subscription)


# --- API endpoints -----------------------------------------------------


def _setup_sqlite_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceEventsApi:

    def test_types_endpoint_returns_known_vocabulary(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-events-types.db")

        response = client.get("/governance/events/types")

        assert response.status_code == 200
        assert set(response.json()) == set(GOVERNANCE_EVENT_TYPES)

    def test_subscribers_endpoint_empty_by_default(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(
            monkeypatch, tmp_path, "api-events-subscribers-empty.db"
        )

        response = client.get("/governance/events/subscribers")

        assert response.status_code == 200
        assert response.json() == []

    def test_subscribers_endpoint_reflects_registered_subscription(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(
            monkeypatch, tmp_path, "api-events-subscribers.db"
        )

        subscription = get_event_bus().subscribe(
            "component_started", lambda e: None
        )

        try:
            response = client.get("/governance/events/subscribers")

            assert response.status_code == 200

            payload = response.json()

            assert len(payload) == 1
            assert payload[0]["event_type"] == "component_started"

        finally:
            get_event_bus().unsubscribe(subscription)
