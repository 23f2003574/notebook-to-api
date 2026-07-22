from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_job_registry import (
    GovernanceJob,
    GovernanceJobRegistry,
    JobRegistrationResult,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The governance job registry and scheduler are both process-wide
    singletons wired together (the scheduler delegates all job
    storage to the registry), so tests that touch either (directly or
    via the API) must not leak state into other tests.
    """

    from backend.observability.deployment_governance_job_registry import (
        get_job_registry,
    )
    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_job_registry().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestGovernanceJob:

    def test_rejects_empty_job_id(self):
        with pytest.raises(ValueError, match="job_id must not be empty"):
            GovernanceJob(
                job_id="", name="a", namespace="default",
                description="", enabled=True, created_at=BASE_TIME,
            )

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            GovernanceJob(
                job_id="1", name="", namespace="default",
                description="", enabled=True, created_at=BASE_TIME,
            )

    def test_rejects_empty_namespace(self):
        with pytest.raises(
            ValueError, match="namespace must not be empty"
        ):
            GovernanceJob(
                job_id="1", name="a", namespace="",
                description="", enabled=True, created_at=BASE_TIME,
            )

    def test_rejects_naive_created_at(self):
        with pytest.raises(
            ValueError, match="created_at must be timezone-aware"
        ):
            GovernanceJob(
                job_id="1", name="a", namespace="default",
                description="", enabled=True,
                created_at=datetime(2026, 7, 21, 12, 0, 0),
            )

    def test_to_dict(self):
        job = GovernanceJob(
            job_id="1", name="a", namespace="default",
            description="does a thing", enabled=False,
            created_at=BASE_TIME,
        )

        assert job.to_dict() == {
            "job_id": "1",
            "name": "a",
            "namespace": "default",
            "description": "does a thing",
            "enabled": False,
            "created_at": BASE_TIME.isoformat(),
        }


class TestJobRegistrationResult:

    def test_rejects_reason_when_accepted(self):
        with pytest.raises(
            ValueError, match="reason must not be set when accepted"
        ):
            JobRegistrationResult(accepted=True, reason="huh")

    def test_rejects_missing_reason_when_rejected(self):
        with pytest.raises(
            ValueError, match="reason must be set when accepted is False"
        ):
            JobRegistrationResult(accepted=False, reason=None)

    def test_to_dict(self):
        result = JobRegistrationResult(accepted=False, reason="nope")

        assert result.to_dict() == {"accepted": False, "reason": "nope"}


# --- Registration --------------------------------------------------------


class TestRegistration:

    def test_register_accepts_new_job(self):
        registry = GovernanceJobRegistry(clock=_clock)

        result = registry.register("job-1", "a")

        assert result.accepted is True
        assert result.reason is None

    def test_registered_job_is_retrievable(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register(
            "job-1", "a", namespace="ns", description="desc",
            enabled=False,
        )

        job = registry.get("job-1")

        assert job.job_id == "job-1"
        assert job.name == "a"
        assert job.namespace == "ns"
        assert job.description == "desc"
        assert job.enabled is False
        assert job.created_at == BASE_TIME

    def test_defaults_namespace_and_enabled(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")

        job = registry.get("job-1")

        assert job.namespace == "default"
        assert job.enabled is True


class TestDuplicateRejection:

    def test_duplicate_job_id_rejected(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")

        result = registry.register("job-1", "b")

        assert result.accepted is False
        assert "job_id" in result.reason
        assert "already registered" in result.reason

    def test_duplicate_name_in_same_namespace_rejected(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a", namespace="ns")

        result = registry.register("job-2", "a", namespace="ns")

        assert result.accepted is False
        assert "already registered" in result.reason

    def test_same_name_in_different_namespace_is_allowed(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a", namespace="ns-one")

        result = registry.register("job-2", "a", namespace="ns-two")

        assert result.accepted is True

    def test_rejected_registration_does_not_store_a_job(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")

        registry.register("job-1", "b")

        assert registry.get("job-1").name == "a"


# --- Lookup ----------------------------------------------------------


class TestLookup:

    def test_get_unknown_job_raises(self):
        registry = GovernanceJobRegistry(clock=_clock)

        with pytest.raises(KeyError):
            registry.get("ghost")

    def test_exists_true_for_registered_job(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")

        assert registry.exists("job-1") is True

    def test_exists_false_for_unknown_job(self):
        registry = GovernanceJobRegistry(clock=_clock)

        assert registry.exists("ghost") is False


# --- Namespace listing -------------------------------------------------


class TestNamespaceListing:

    def test_list_namespace_filters_by_namespace(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a", namespace="ns-one")
        registry.register("job-2", "b", namespace="ns-two")

        names = [job.name for job in registry.list_namespace("ns-one")]

        assert names == ["a"]

    def test_list_namespace_empty_for_unknown_namespace(self):
        registry = GovernanceJobRegistry(clock=_clock)

        assert registry.list_namespace("ghost-namespace") == ()


# --- Enable / disable ----------------------------------------------------


class TestEnableDisable:

    def test_disable_marks_job_disabled(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")

        job = registry.disable("job-1")

        assert job.enabled is False
        assert registry.get("job-1").enabled is False

    def test_enable_marks_job_enabled(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a", enabled=False)

        job = registry.enable("job-1")

        assert job.enabled is True

    def test_enable_unknown_job_raises(self):
        registry = GovernanceJobRegistry(clock=_clock)

        with pytest.raises(KeyError):
            registry.enable("ghost")

    def test_disable_unknown_job_raises(self):
        registry = GovernanceJobRegistry(clock=_clock)

        with pytest.raises(KeyError):
            registry.disable("ghost")

    def test_disable_preserves_every_other_field(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register(
            "job-1", "a", namespace="ns", description="desc",
        )

        job = registry.disable("job-1")

        assert job.name == "a"
        assert job.namespace == "ns"
        assert job.description == "desc"
        assert job.created_at == BASE_TIME


# --- Rename ------------------------------------------------------------


class TestRename:

    def test_rename_changes_the_name(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")

        job = registry.rename("job-1", "b")

        assert job.name == "b"
        assert registry.get("job-1").name == "b"

    def test_rename_frees_the_old_name(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")
        registry.rename("job-1", "b")

        result = registry.register("job-2", "a")

        assert result.accepted is True

    def test_rename_unknown_job_raises(self):
        registry = GovernanceJobRegistry(clock=_clock)

        with pytest.raises(KeyError):
            registry.rename("ghost", "b")

    def test_rename_to_existing_name_in_namespace_raises(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a", namespace="ns")
        registry.register("job-2", "b", namespace="ns")

        with pytest.raises(ValueError, match="already registered"):
            registry.rename("job-2", "a")

    def test_rename_to_same_name_is_a_no_op(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")

        job = registry.rename("job-1", "a")

        assert job.name == "a"


# --- Unregister ----------------------------------------------------------


class TestUnregister:

    def test_unregister_removes_job(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")

        registry.unregister("job-1")

        assert registry.exists("job-1") is False

    def test_unregister_unknown_job_raises(self):
        registry = GovernanceJobRegistry(clock=_clock)

        with pytest.raises(KeyError):
            registry.unregister("ghost")

    def test_unregister_frees_the_name_for_reuse(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-1", "a")
        registry.unregister("job-1")

        result = registry.register("job-2", "a")

        assert result.accepted is True


# --- Deterministic ordering -----------------------------------------------


class TestDeterministicOrdering:

    def test_list_ordered_by_namespace_then_name_then_job_id(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-z", "b", namespace="ns-two")
        registry.register("job-y", "b", namespace="ns-one")
        registry.register("job-x", "a", namespace="ns-one")

        ordering = [
            (job.namespace, job.name, job.job_id)
            for job in registry.list()
        ]

        assert ordering == [
            ("ns-one", "a", "job-x"),
            ("ns-one", "b", "job-y"),
            ("ns-two", "b", "job-z"),
        ]

    def test_list_namespace_ordered_by_name_then_job_id(self):
        registry = GovernanceJobRegistry(clock=_clock)
        registry.register("job-z", "b", namespace="ns")
        registry.register("job-y", "a", namespace="ns")

        job_ids = [job.job_id for job in registry.list_namespace("ns")]

        assert job_ids == ["job-y", "job-z"]


# --- Clear -----------------------------------------------------------


def test_clear_removes_every_job():
    registry = GovernanceJobRegistry(clock=_clock)
    registry.register("job-1", "a")
    registry.register("job-2", "b")

    registry.clear()

    assert registry.list() == ()


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_registration_publishes_job_registry_registered(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        registry = GovernanceJobRegistry(clock=_clock, event_bus=bus)
        registry.register("job-1", "a")

        assert received == ["job_registry_registered"]

    def test_rejected_registration_publishes_nothing(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        registry = GovernanceJobRegistry(clock=_clock, event_bus=bus)
        registry.register("job-1", "a")
        received.clear()

        registry.register("job-1", "b")

        assert received == []

    def test_unregister_publishes_job_registry_removed(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        registry = GovernanceJobRegistry(clock=_clock, event_bus=bus)
        registry.register("job-1", "a")
        received.clear()

        registry.unregister("job-1")

        assert received == ["job_registry_removed"]

    def test_enable_disable_publish_matching_events(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        registry = GovernanceJobRegistry(clock=_clock, event_bus=bus)
        registry.register("job-1", "a")
        received.clear()

        registry.disable("job-1")
        registry.enable("job-1")

        assert received == ["job_disabled", "job_enabled"]


# --- Scheduler delegation -------------------------------------------------


class TestSchedulerDelegation:

    def test_scheduler_delegates_registration_to_its_registry(self):
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        registry = GovernanceJobRegistry(clock=_clock)
        scheduler = GovernanceScheduler(clock=_clock, job_registry=registry)

        job = scheduler.register("a", interval_seconds=60)

        assert registry.exists(job.job_id) is True
        assert registry.get(job.job_id).name == "a"

    def test_scheduler_unregister_removes_from_registry_too(self):
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        registry = GovernanceJobRegistry(clock=_clock)
        scheduler = GovernanceScheduler(clock=_clock, job_registry=registry)
        job = scheduler.register("a", interval_seconds=60)

        scheduler.unregister(job.job_id)

        assert registry.exists(job.job_id) is False

    def test_scheduler_duplicate_registration_surfaces_registry_reason(
        self,
    ):
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        registry = GovernanceJobRegistry(clock=_clock)
        scheduler = GovernanceScheduler(clock=_clock, job_registry=registry)
        scheduler.register("a", interval_seconds=60)

        with pytest.raises(ValueError, match="already registered"):
            scheduler.register("a", interval_seconds=30)

    def test_standalone_scheduler_has_its_own_private_registry(self):
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )

        first = GovernanceScheduler(clock=_clock)
        second = GovernanceScheduler(clock=_clock)

        first.register("a", interval_seconds=60)

        assert second.register("a", interval_seconds=60).name == "a"


# --- Singleton -------------------------------------------------------------


class TestJobRegistrySingleton:

    def test_get_job_registry_returns_same_instance(self):
        from backend.observability.deployment_governance_job_registry import (
            get_job_registry,
        )

        assert get_job_registry() is get_job_registry()

    def test_default_scheduler_shares_the_singleton_registry(self):
        from backend.observability.deployment_governance_job_registry import (
            get_job_registry,
        )
        from backend.observability.deployment_governance_scheduler import (
            get_scheduler,
        )

        job = get_scheduler().register("shared-job", interval_seconds=60)

        try:
            assert get_job_registry().exists(job.job_id) is True

        finally:
            get_scheduler().unregister(job.job_id)


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceJobRegistryApi:

    def test_get_jobs_returns_empty_list_initially(self, client) -> None:
        response = client.get("/governance/jobs")

        assert response.status_code == 200
        assert response.json() == []

    def test_get_job_by_id(self, client) -> None:
        from backend.observability.deployment_governance_job_registry import (
            get_job_registry,
        )

        get_job_registry().register("job-1", "a")

        response = client.get("/governance/jobs/job-1")

        assert response.status_code == 200
        assert response.json()["name"] == "a"

    def test_get_unknown_job_returns_404(self, client) -> None:
        response = client.get("/governance/jobs/ghost")

        assert response.status_code == 404

    def test_get_jobs_namespace(self, client) -> None:
        from backend.observability.deployment_governance_job_registry import (
            get_job_registry,
        )

        get_job_registry().register("job-1", "a", namespace="ns")
        get_job_registry().register("job-2", "b", namespace="other")

        response = client.get("/governance/jobs/namespace/ns")

        assert response.status_code == 200

        names = [job["name"] for job in response.json()]

        assert names == ["a"]

    def test_patch_enable_disable(self, client) -> None:
        from backend.observability.deployment_governance_job_registry import (
            get_job_registry,
        )

        get_job_registry().register("job-1", "a", enabled=False)

        response = client.patch("/governance/jobs/job-1/enable")

        assert response.status_code == 200
        assert response.json()["enabled"] is True

        response = client.patch("/governance/jobs/job-1/disable")

        assert response.status_code == 200
        assert response.json()["enabled"] is False

    def test_patch_enable_unknown_job_returns_404(self, client) -> None:
        response = client.patch("/governance/jobs/ghost/enable")

        assert response.status_code == 404

    def test_delete_job(self, client) -> None:
        from backend.observability.deployment_governance_job_registry import (
            get_job_registry,
        )

        get_job_registry().register("job-1", "a")

        response = client.delete("/governance/jobs/job-1")

        assert response.status_code == 200
        assert response.json() == {"removed": "job-1"}
        assert get_job_registry().exists("job-1") is False

    def test_delete_unknown_job_returns_404(self, client) -> None:
        response = client.delete("/governance/jobs/ghost")

        assert response.status_code == 404
