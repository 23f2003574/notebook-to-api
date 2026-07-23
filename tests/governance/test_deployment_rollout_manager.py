from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEventBus,
)
from backend.observability.deployment_governance_rollout_manager import (
    DeploymentRolloutManager,
    Rollout,
    RolloutStatus,
    get_rollout_manager,
)
from backend.observability.deployment_governance_version_registry import (
    get_version_registry,
)

BASE_TIME = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)

VALID_CHECKSUM = "a" * 64


def _clock():
    return BASE_TIME


def _manager(event_bus=None) -> DeploymentRolloutManager:
    return DeploymentRolloutManager(clock=_clock, event_bus=event_bus)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """
    The rollout manager and version registry are both process-wide
    singletons; most tests below construct their own fresh manager
    instead (see _manager), and only the singleton and API tests touch
    the shared instances, matching
    test_deployment_governance_scheduler_bootstrap.py's own fixture.

    The process-wide rollout manager is wired to the process-wide
    version registry (see build_default_governance_rollout_manager),
    so API tests that create rollouts through it must first register
    the deployment there.
    """

    def _reset():
        get_rollout_manager().clear()
        get_version_registry().clear()

    _reset()
    yield
    _reset()


# --- Models ------------------------------------------------------------


class TestRollout:

    def test_rejects_empty_rollout_id(self):
        with pytest.raises(ValueError, match="rollout_id must not be empty"):
            Rollout(
                rollout_id="", deployment_id="dep-1", strategy="CANARY",
                state="PENDING", created_at=BASE_TIME,
            )

    def test_rejects_unknown_strategy(self):
        with pytest.raises(ValueError, match="strategy must be one of"):
            Rollout(
                rollout_id="r-1", deployment_id="dep-1", strategy="BOGUS",
                state="PENDING", created_at=BASE_TIME,
            )

    def test_rejects_unknown_state(self):
        with pytest.raises(ValueError, match="state must be one of"):
            Rollout(
                rollout_id="r-1", deployment_id="dep-1", strategy="CANARY",
                state="BOGUS", created_at=BASE_TIME,
            )

    def test_rejects_naive_created_at(self):
        with pytest.raises(
            ValueError, match="created_at must be timezone-aware"
        ):
            Rollout(
                rollout_id="r-1", deployment_id="dep-1", strategy="CANARY",
                state="PENDING", created_at=datetime(2026, 7, 23, 12, 0, 0),
            )

    def test_to_dict(self):
        rollout = Rollout(
            rollout_id="r-1", deployment_id="dep-1", strategy="CANARY",
            state="PENDING", created_at=BASE_TIME,
        )

        assert rollout.to_dict() == {
            "rollout_id": "r-1",
            "deployment_id": "dep-1",
            "strategy": "CANARY",
            "state": "PENDING",
            "created_at": BASE_TIME.isoformat(),
        }


class TestRolloutStatus:

    def test_rejects_progress_out_of_range(self):
        with pytest.raises(
            ValueError, match="progress must be between 0.0 and 1.0"
        ):
            RolloutStatus(
                rollout_id="r-1", state="RUNNING", progress=1.5,
                current_stage="in_progress", updated_at=BASE_TIME,
            )

    def test_rejects_naive_updated_at(self):
        with pytest.raises(
            ValueError, match="updated_at must be timezone-aware"
        ):
            RolloutStatus(
                rollout_id="r-1", state="RUNNING", progress=0.5,
                current_stage="in_progress",
                updated_at=datetime(2026, 7, 23, 12, 0, 0),
            )


# --- Rollout creation ----------------------------------------------------


class TestCreate:

    def test_create_returns_pending_rollout(self):
        manager = _manager()

        rollout = manager.create("dep-1", "CANARY")

        assert rollout.deployment_id == "dep-1"
        assert rollout.strategy == "CANARY"
        assert rollout.state == "PENDING"
        assert rollout.created_at == BASE_TIME

    def test_create_assigns_a_uuid_rollout_id(self):
        manager = _manager()

        rollout = manager.create("dep-1", "CANARY")

        assert len(rollout.rollout_id) == 36
        assert rollout.rollout_id.count("-") == 4

    def test_create_rejects_duplicate_active_rollout(self):
        manager = _manager()

        manager.create("dep-1", "CANARY")

        with pytest.raises(
            ValueError, match="already has an active rollout"
        ):
            manager.create("dep-1", "ROLLING")

    def test_create_allows_new_rollout_after_previous_completes(self):
        manager = _manager()

        first = manager.create("dep-1", "CANARY")
        manager.start(first.rollout_id)
        manager.complete(first.rollout_id)

        second = manager.create("dep-1", "ROLLING")

        assert second.rollout_id != first.rollout_id
        assert second.state == "PENDING"

    def test_create_allows_concurrent_rollouts_for_different_deployments(
        self,
    ):
        manager = _manager()

        first = manager.create("dep-1", "CANARY")
        second = manager.create("dep-2", "ROLLING")

        assert first.rollout_id != second.rollout_id

    def test_create_publishes_rollout_created(self):
        bus = GovernanceEventBus(clock=_clock)
        manager = _manager(event_bus=bus)

        events = []
        bus.subscribe("rollout_created", events.append)

        rollout = manager.create("dep-1", "CANARY")

        assert len(events) == 1
        assert events[0].source == rollout.rollout_id
        assert events[0].payload["deployment_id"] == "dep-1"


class TestVersionRegistryIntegration:

    def test_create_with_no_registry_wired_accepts_any_deployment_id(
        self,
    ):
        manager = _manager()

        rollout = manager.create("unresolved-dep", "CANARY")

        assert rollout.deployment_id == "unresolved-dep"

    def test_create_rejects_unregistered_deployment(self):
        from backend.observability.deployment_governance_version_registry import (  # noqa: E501
            DeploymentVersionRegistry,
        )

        registry = DeploymentVersionRegistry(clock=_clock)
        manager = DeploymentRolloutManager(
            clock=_clock, version_registry=registry
        )

        with pytest.raises(
            ValueError, match="not registered in the version registry"
        ):
            manager.create("dep-1", "CANARY")

    def test_create_accepts_registered_deployment(self):
        from backend.observability.deployment_governance_version_registry import (  # noqa: E501
            DeploymentVersionRegistry,
        )

        registry = DeploymentVersionRegistry(clock=_clock)
        registry.register("dep-1", "1.0.0", "artifact.tar.gz", VALID_CHECKSUM)

        manager = DeploymentRolloutManager(
            clock=_clock, version_registry=registry
        )

        rollout = manager.create("dep-1", "CANARY")

        assert rollout.deployment_id == "dep-1"

    def test_singleton_rollout_manager_is_wired_to_singleton_registry(
        self,
    ):
        get_version_registry().register(
            "dep-singleton-wiring",
            "1.0.0",
            "artifact.tar.gz",
            VALID_CHECKSUM,
        )

        rollout = get_rollout_manager().create(
            "dep-singleton-wiring", "CANARY"
        )

        assert rollout.deployment_id == "dep-singleton-wiring"


# --- Lifecycle transitions -----------------------------------------------


class TestLifecycleTransitions:

    def test_start_transitions_to_running(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")

        started = manager.start(rollout.rollout_id)

        assert started.state == "RUNNING"

    def test_pause_transitions_to_paused(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)

        paused = manager.pause(rollout.rollout_id)

        assert paused.state == "PAUSED"

    def test_resume_transitions_back_to_running(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)
        manager.pause(rollout.rollout_id)

        resumed = manager.resume(rollout.rollout_id)

        assert resumed.state == "RUNNING"

    def test_complete_transitions_to_completed_with_full_progress(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)

        completed = manager.complete(rollout.rollout_id)

        assert completed.state == "COMPLETED"
        assert manager.status(rollout.rollout_id).progress == 1.0

    def test_cancel_from_pending(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")

        cancelled = manager.cancel(rollout.rollout_id)

        assert cancelled.state == "CANCELLED"

    def test_cancel_from_running(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)

        cancelled = manager.cancel(rollout.rollout_id)

        assert cancelled.state == "CANCELLED"

    def test_cancel_releases_the_active_deployment_slot(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")

        manager.cancel(rollout.rollout_id)

        second = manager.create("dep-1", "ROLLING")

        assert second.state == "PENDING"

    def test_fail_transitions_to_failed(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)

        failed = manager.fail(rollout.rollout_id, reason="boom")

        assert failed.state == "FAILED"


class TestInvalidTransitions:

    def test_pause_before_start_is_rejected(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")

        with pytest.raises(ValueError, match="cannot transition"):
            manager.pause(rollout.rollout_id)

    def test_resume_while_already_running_is_idempotent(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)

        resumed = manager.resume(rollout.rollout_id)

        assert resumed.state == "RUNNING"

    def test_resume_from_pending_is_rejected(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")

        with pytest.raises(ValueError, match="cannot transition"):
            manager.resume(rollout.rollout_id)

    def test_start_after_completion_is_rejected(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)
        manager.complete(rollout.rollout_id)

        with pytest.raises(ValueError, match="cannot transition"):
            manager.start(rollout.rollout_id)

    def test_cancel_after_completion_is_rejected(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)
        manager.complete(rollout.rollout_id)

        with pytest.raises(ValueError, match="cannot transition"):
            manager.cancel(rollout.rollout_id)

    def test_transition_on_unknown_rollout_raises_key_error(self):
        manager = _manager()

        with pytest.raises(KeyError):
            manager.start("does-not-exist")


class TestIdempotentTransitions:

    def test_start_twice_is_a_no_op(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")

        first = manager.start(rollout.rollout_id)
        second = manager.start(rollout.rollout_id)

        assert first == second

    def test_cancel_twice_is_a_no_op(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")

        manager.cancel(rollout.rollout_id)
        second = manager.cancel(rollout.rollout_id)

        assert second.state == "CANCELLED"

    def test_idempotent_start_publishes_nothing_further(self):
        bus = GovernanceEventBus(clock=_clock)
        manager = _manager(event_bus=bus)
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)

        events = []
        bus.subscribe("rollout_started", events.append)

        manager.start(rollout.rollout_id)

        assert events == []


# --- Status retrieval ------------------------------------------------------


class TestStatus:

    def test_status_reflects_current_state(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)

        status = manager.status(rollout.rollout_id)

        assert status.state == "RUNNING"
        assert status.rollout_id == rollout.rollout_id

    def test_status_for_unknown_rollout_raises_key_error(self):
        manager = _manager()

        with pytest.raises(KeyError):
            manager.status("does-not-exist")

    def test_status_is_immutable_snapshot(self):
        manager = _manager()
        rollout = manager.create("dep-1", "CANARY")

        first = manager.status(rollout.rollout_id)
        manager.start(rollout.rollout_id)
        second = manager.status(rollout.rollout_id)

        assert first.state == "PENDING"
        assert second.state == "RUNNING"
        assert first != second


class TestList:

    def test_list_orders_by_created_at_then_rollout_id(self):
        times = iter(
            [
                BASE_TIME,
                BASE_TIME.replace(minute=1),
            ]
        )
        manager = DeploymentRolloutManager(clock=lambda: next(times))

        first = manager.create("dep-1", "CANARY")
        second = manager.create("dep-2", "ROLLING")

        listed = manager.list()

        assert [rollout.rollout_id for rollout in listed] == [
            first.rollout_id, second.rollout_id,
        ]

    def test_list_orders_by_rollout_id_when_created_at_ties(self):
        manager = _manager()

        first = manager.create("dep-1", "CANARY")
        second = manager.create("dep-2", "ROLLING")

        listed = manager.list()

        assert [rollout.rollout_id for rollout in listed] == sorted(
            [first.rollout_id, second.rollout_id]
        )

    def test_list_is_empty_when_nothing_registered(self):
        manager = _manager()

        assert manager.list() == ()


# --- Event publication -----------------------------------------------------


class TestEventPublication:

    @pytest.mark.parametrize(
        "operation,event_type",
        [
            ("start", "rollout_started"),
            ("pause", "rollout_paused"),
            ("resume", "rollout_resumed"),
            ("complete", "rollout_completed"),
            ("cancel", "rollout_cancelled"),
        ],
    )
    def test_transition_publishes_expected_event(
        self, operation, event_type
    ):
        bus = GovernanceEventBus(clock=_clock)
        manager = _manager(event_bus=bus)
        rollout = manager.create("dep-1", "CANARY")

        events = []
        bus.subscribe(event_type, events.append)

        if operation == "pause":
            manager.start(rollout.rollout_id)
        elif operation == "resume":
            manager.start(rollout.rollout_id)
            manager.pause(rollout.rollout_id)
        elif operation == "complete":
            manager.start(rollout.rollout_id)

        getattr(manager, operation)(rollout.rollout_id)

        assert len(events) == 1
        assert events[0].source == rollout.rollout_id

    def test_no_event_bus_is_safe(self):
        manager = _manager(event_bus=None)

        rollout = manager.create("dep-1", "CANARY")
        manager.start(rollout.rollout_id)
        manager.complete(rollout.rollout_id)


# --- Singleton ---------------------------------------------------------


class TestSingleton:

    def test_get_rollout_manager_returns_same_instance(self):
        assert get_rollout_manager() is get_rollout_manager()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


def _register(client: TestClient, deployment_id: str) -> None:
    """
    The process-wide rollout manager resolves deployment_id against
    the process-wide version registry (see
    build_default_governance_rollout_manager), so every API test
    below that creates a rollout must register its deployment_id
    first.
    """

    response = client.post(
        "/governance/deployments",
        params={
            "deployment_id": deployment_id,
            "version": "1.0.0",
            "artifact": "artifact.tar.gz",
            "checksum": VALID_CHECKSUM,
        },
    )

    assert response.status_code == 200


class TestGovernanceRolloutApi:

    def test_post_creates_rollout(self, client):
        _register(client, "dep-api-1")

        response = client.post(
            "/governance/rollouts",
            params={"deployment_id": "dep-api-1", "strategy": "CANARY"},
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["deployment_id"] == "dep-api-1"
        assert payload["state"] == "PENDING"

    def test_post_unregistered_deployment_returns_409(self, client):
        response = client.post(
            "/governance/rollouts",
            params={
                "deployment_id": "dep-api-unregistered",
                "strategy": "CANARY",
            },
        )

        assert response.status_code == 409

    def test_post_duplicate_active_rollout_returns_409(self, client):
        _register(client, "dep-api-2")

        client.post(
            "/governance/rollouts",
            params={"deployment_id": "dep-api-2", "strategy": "CANARY"},
        )

        response = client.post(
            "/governance/rollouts",
            params={"deployment_id": "dep-api-2", "strategy": "ROLLING"},
        )

        assert response.status_code == 409

    def test_get_rollout_status(self, client):
        _register(client, "dep-api-3")

        created = client.post(
            "/governance/rollouts",
            params={"deployment_id": "dep-api-3", "strategy": "CANARY"},
        ).json()

        response = client.get(
            f"/governance/rollouts/{created['rollout_id']}"
        )

        assert response.status_code == 200
        assert response.json()["state"] == "PENDING"

    def test_get_unknown_rollout_returns_404(self, client):
        response = client.get("/governance/rollouts/does-not-exist")

        assert response.status_code == 404

    def test_list_rollouts(self, client):
        _register(client, "dep-api-4")

        client.post(
            "/governance/rollouts",
            params={"deployment_id": "dep-api-4", "strategy": "CANARY"},
        )

        response = client.get("/governance/rollouts")

        assert response.status_code == 200
        assert any(
            rollout["deployment_id"] == "dep-api-4"
            for rollout in response.json()
        )

    def test_start_pause_resume_cycle(self, client):
        _register(client, "dep-api-5")

        created = client.post(
            "/governance/rollouts",
            params={"deployment_id": "dep-api-5", "strategy": "ROLLING"},
        ).json()
        rollout_id = created["rollout_id"]

        start_response = client.post(
            f"/governance/rollouts/{rollout_id}/start"
        )
        assert start_response.json()["state"] == "RUNNING"

        pause_response = client.post(
            f"/governance/rollouts/{rollout_id}/pause"
        )
        assert pause_response.json()["state"] == "PAUSED"

        resume_response = client.post(
            f"/governance/rollouts/{rollout_id}/resume"
        )
        assert resume_response.json()["state"] == "RUNNING"

    def test_delete_cancels_rollout(self, client):
        _register(client, "dep-api-6")

        created = client.post(
            "/governance/rollouts",
            params={"deployment_id": "dep-api-6", "strategy": "CANARY"},
        ).json()
        rollout_id = created["rollout_id"]

        response = client.delete(f"/governance/rollouts/{rollout_id}")

        assert response.status_code == 200
        assert response.json()["state"] == "CANCELLED"

    def test_delete_unknown_rollout_returns_404(self, client):
        response = client.delete("/governance/rollouts/does-not-exist")

        assert response.status_code == 404

    def test_invalid_transition_returns_409(self, client):
        _register(client, "dep-api-7")

        created = client.post(
            "/governance/rollouts",
            params={"deployment_id": "dep-api-7", "strategy": "CANARY"},
        ).json()
        rollout_id = created["rollout_id"]

        response = client.post(
            f"/governance/rollouts/{rollout_id}/pause"
        )

        assert response.status_code == 409
