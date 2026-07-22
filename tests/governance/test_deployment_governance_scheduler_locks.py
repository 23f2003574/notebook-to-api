from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_scheduler_locks import (
    FileLockProvider,
    GovernanceSchedulerLockManager,
    InMemoryLockProvider,
    LockAcquisitionResult,
    SchedulerLock,
    build_lock_provider,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


class _ControllableClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The scheduler lock manager is a process-wide singleton with no
    bulk "clear everything" method (there is no reset concept for a
    distributed lock — a lease is meant to expire, not be wiped), so
    every currently stored lock is released individually instead.
    """

    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )
    from backend.observability.deployment_governance_scheduler_locks import (
        get_scheduler_lock_manager,
    )

    def _reset():
        get_lifecycle_manager().shutdown()

        lock_manager = get_scheduler_lock_manager()

        for lock in lock_manager.list():
            lock_manager.release(lock.job_id, lock.owner_id)

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestSchedulerLock:

    def test_rejects_empty_job_id(self):
        with pytest.raises(ValueError, match="job_id must not be empty"):
            SchedulerLock(
                job_id="", owner_id="node-1", acquired_at=BASE_TIME,
                expires_at=BASE_TIME + timedelta(seconds=30),
            )

    def test_rejects_empty_owner_id(self):
        with pytest.raises(ValueError, match="owner_id must not be empty"):
            SchedulerLock(
                job_id="job-1", owner_id="", acquired_at=BASE_TIME,
                expires_at=BASE_TIME + timedelta(seconds=30),
            )

    def test_rejects_naive_acquired_at(self):
        with pytest.raises(
            ValueError, match="acquired_at must be timezone-aware"
        ):
            SchedulerLock(
                job_id="job-1", owner_id="node-1",
                acquired_at=datetime(2026, 7, 21, 12, 0, 0),
                expires_at=BASE_TIME + timedelta(seconds=30),
            )

    def test_rejects_expires_at_before_acquired_at(self):
        with pytest.raises(
            ValueError, match="expires_at must be after acquired_at"
        ):
            SchedulerLock(
                job_id="job-1", owner_id="node-1", acquired_at=BASE_TIME,
                expires_at=BASE_TIME - timedelta(seconds=1),
            )

    def test_to_dict(self):
        lock = SchedulerLock(
            job_id="job-1", owner_id="node-1", acquired_at=BASE_TIME,
            expires_at=BASE_TIME + timedelta(seconds=30),
        )

        assert lock.to_dict() == {
            "job_id": "job-1",
            "owner_id": "node-1",
            "acquired_at": BASE_TIME.isoformat(),
            "expires_at": (BASE_TIME + timedelta(seconds=30)).isoformat(),
        }


class TestLockAcquisitionResult:

    def test_rejects_acquired_without_owner_id(self):
        with pytest.raises(
            ValueError, match="owner_id must be set when acquired is True"
        ):
            LockAcquisitionResult(
                acquired=True, owner_id=None, expires_at=BASE_TIME,
            )

    def test_rejects_acquired_without_expires_at(self):
        with pytest.raises(
            ValueError, match="expires_at must be set when acquired is True"
        ):
            LockAcquisitionResult(
                acquired=True, owner_id="node-1", expires_at=None,
            )

    def test_to_dict(self):
        result = LockAcquisitionResult(
            acquired=True, owner_id="node-1", expires_at=BASE_TIME,
        )

        assert result.to_dict() == {
            "acquired": True,
            "owner_id": "node-1",
            "expires_at": BASE_TIME.isoformat(),
        }


# --- Acquire lock --------------------------------------------------------


class TestAcquireLock:

    def test_acquire_succeeds_when_unheld(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)

        result = manager.acquire("job-1", "node-1")

        assert result.acquired is True
        assert result.owner_id == "node-1"
        assert result.expires_at == BASE_TIME + timedelta(seconds=30)

    def test_acquire_uses_configured_default_lease(self):
        manager = GovernanceSchedulerLockManager(
            clock=_clock, lease_seconds=10,
        )

        result = manager.acquire("job-1", "node-1")

        assert result.expires_at == BASE_TIME + timedelta(seconds=10)

    def test_acquire_accepts_explicit_lease_override(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)

        result = manager.acquire("job-1", "node-1", lease_seconds=5)

        assert result.expires_at == BASE_TIME + timedelta(seconds=5)

    def test_reacquire_by_same_owner_succeeds(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")

        result = manager.acquire("job-1", "node-1")

        assert result.acquired is True

    def test_rejects_non_positive_lease_seconds(self):
        with pytest.raises(ValueError, match="lease_seconds must be > 0"):
            GovernanceSchedulerLockManager(clock=_clock, lease_seconds=0)

    def test_acquired_lock_is_locked(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")

        assert manager.is_locked("job-1") is True

    def test_acquired_lock_reports_owner(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")

        assert manager.owner("job-1") == "node-1"

    def test_unlocked_job_has_no_owner(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)

        assert manager.owner("job-1") is None
        assert manager.is_locked("job-1") is False


# --- Acquire already-held lock ------------------------------------------


class TestAcquireAlreadyHeldLock:

    def test_different_owner_is_rejected(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")

        result = manager.acquire("job-1", "node-2")

        assert result.acquired is False
        assert result.owner_id == "node-1"

    def test_contention_does_not_change_the_holder(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")

        manager.acquire("job-1", "node-2")

        assert manager.owner("job-1") == "node-1"

    def test_contention_after_expiry_succeeds_for_new_owner(self):
        clock = _ControllableClock(BASE_TIME)
        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=10,
        )
        manager.acquire("job-1", "node-1")

        clock.now = BASE_TIME + timedelta(seconds=11)

        result = manager.acquire("job-1", "node-2")

        assert result.acquired is True
        assert result.owner_id == "node-2"


# --- Release lock --------------------------------------------------------


class TestReleaseLock:

    def test_release_by_owner_succeeds(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")

        released = manager.release("job-1", "node-1")

        assert released is True
        assert manager.is_locked("job-1") is False

    def test_release_by_non_owner_is_a_no_op(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")

        released = manager.release("job-1", "node-2")

        assert released is False
        assert manager.owner("job-1") == "node-1"

    def test_release_of_unheld_lock_is_idempotent(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)

        # Should not raise.
        released = manager.release("job-1", "node-1")

        assert released is False

    def test_double_release_is_idempotent(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")
        manager.release("job-1", "node-1")

        released_again = manager.release("job-1", "node-1")

        assert released_again is False

    def test_released_lock_can_be_reacquired_by_anyone(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")
        manager.release("job-1", "node-1")

        result = manager.acquire("job-1", "node-2")

        assert result.acquired is True


# --- Lock expiration ----------------------------------------------------


class TestLockExpiration:

    def test_expired_lock_is_not_locked(self):
        clock = _ControllableClock(BASE_TIME)
        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=10,
        )
        manager.acquire("job-1", "node-1")

        clock.now = BASE_TIME + timedelta(seconds=11)

        assert manager.is_locked("job-1") is False
        assert manager.owner("job-1") is None

    def test_expired_returns_stale_locks(self):
        clock = _ControllableClock(BASE_TIME)
        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=10,
        )
        manager.acquire("job-1", "node-1")

        clock.now = BASE_TIME + timedelta(seconds=11)

        stale = manager.expired()

        assert len(stale) == 1
        assert stale[0].job_id == "job-1"

    def test_expired_excludes_still_valid_locks(self):
        clock = _ControllableClock(BASE_TIME)
        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=100,
        )
        manager.acquire("job-1", "node-1")

        assert manager.expired() == ()

    def test_expired_lock_still_appears_in_list(self):
        clock = _ControllableClock(BASE_TIME)
        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=10,
        )
        manager.acquire("job-1", "node-1")

        clock.now = BASE_TIME + timedelta(seconds=11)

        assert len(manager.list()) == 1


# --- Lease renewal -----------------------------------------------------


class TestLeaseRenewal:

    def test_renew_extends_expiry(self):
        clock = _ControllableClock(BASE_TIME)
        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=30,
        )
        manager.acquire("job-1", "node-1")

        clock.now = BASE_TIME + timedelta(seconds=10)
        result = manager.renew("job-1", "node-1")

        assert result.acquired is True
        assert result.expires_at == clock.now + timedelta(seconds=30)

    def test_renew_preserves_acquired_at(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")

        manager.renew("job-1", "node-1")

        [lock] = manager.list()
        assert lock.acquired_at == BASE_TIME

    def test_renew_by_non_owner_fails(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)
        manager.acquire("job-1", "node-1")

        result = manager.renew("job-1", "node-2")

        assert result.acquired is False

    def test_renew_unheld_lock_fails(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)

        result = manager.renew("job-1", "node-1")

        assert result.acquired is False
        assert result.owner_id is None

    def test_renew_expired_lock_fails(self):
        clock = _ControllableClock(BASE_TIME)
        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=10,
        )
        manager.acquire("job-1", "node-1")

        clock.now = BASE_TIME + timedelta(seconds=11)
        result = manager.renew("job-1", "node-1")

        assert result.acquired is False

    def test_renew_accepts_explicit_lease_override(self):
        clock = _ControllableClock(BASE_TIME)
        manager = GovernanceSchedulerLockManager(clock=clock)
        manager.acquire("job-1", "node-1")

        result = manager.renew("job-1", "node-1", lease_seconds=5)

        assert result.expires_at == BASE_TIME + timedelta(seconds=5)


# --- Cleanup expired locks -------------------------------------------


class TestCleanupExpiredLocks:

    def test_cleanup_removes_expired_locks(self):
        clock = _ControllableClock(BASE_TIME)
        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=10,
        )
        manager.acquire("job-1", "node-1")

        clock.now = BASE_TIME + timedelta(seconds=11)
        removed = manager.cleanup()

        assert removed == 1
        assert manager.list() == ()

    def test_cleanup_leaves_valid_locks(self):
        clock = _ControllableClock(BASE_TIME)
        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=100,
        )
        manager.acquire("job-1", "node-1")

        removed = manager.cleanup()

        assert removed == 0
        assert len(manager.list()) == 1

    def test_cleanup_is_deterministic(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        clock = _ControllableClock(BASE_TIME)
        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.source))

        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=10, event_bus=bus,
        )
        manager.acquire("z-job", "node-1")
        manager.acquire("a-job", "node-1")
        received.clear()

        clock.now = BASE_TIME + timedelta(seconds=11)

        manager.cleanup()

        assert received == ["a-job", "z-job"]

    def test_cleanup_publishes_lock_expired_per_lock(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        clock = _ControllableClock(BASE_TIME)
        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceSchedulerLockManager(
            clock=clock, lease_seconds=10, event_bus=bus,
        )
        manager.acquire("job-1", "node-1")
        received.clear()

        clock.now = BASE_TIME + timedelta(seconds=11)
        manager.cleanup()

        assert received == ["lock_expired"]


# --- Provider abstraction -------------------------------------------


class TestProviderAbstraction:

    def test_default_provider_is_in_memory(self):
        manager = GovernanceSchedulerLockManager(clock=_clock)

        assert isinstance(manager.provider, InMemoryLockProvider)
        assert manager.provider.config() == {"type": "memory"}

    def test_file_provider_round_trips_through_disk(self, tmp_path):
        path = tmp_path / "locks.json"
        provider = FileLockProvider(path=path)
        manager = GovernanceSchedulerLockManager(
            clock=_clock, provider=provider,
        )

        manager.acquire("job-1", "node-1")

        # A second manager pointed at the same file sees the same lock
        # — the whole point of a file-backed provider being sharable.
        other_manager = GovernanceSchedulerLockManager(
            clock=_clock, provider=FileLockProvider(path=path),
        )

        assert other_manager.owner("job-1") == "node-1"

    def test_file_provider_config(self, tmp_path):
        path = tmp_path / "locks.json"
        provider = FileLockProvider(path=path)

        assert provider.config() == {"type": "file", "path": str(path)}

    def test_file_provider_handles_missing_file(self, tmp_path):
        provider = FileLockProvider(path=tmp_path / "does-not-exist.json")

        assert provider.read("job-1") is None
        assert provider.list() == ()

    def test_file_provider_handles_corrupted_file(self, tmp_path):
        path = tmp_path / "locks.json"
        path.write_text("not valid json {{{")

        provider = FileLockProvider(path=path)

        assert provider.read("job-1") is None
        assert provider.list() == ()

    def test_build_lock_provider_memory(self):
        provider = build_lock_provider({"type": "memory"})

        assert isinstance(provider, InMemoryLockProvider)

    def test_build_lock_provider_file(self, tmp_path):
        path = tmp_path / "locks.json"
        provider = build_lock_provider({"type": "file", "path": str(path)})

        assert isinstance(provider, FileLockProvider)
        assert provider.config()["path"] == str(path)

    def test_build_lock_provider_unknown_type_raises(self):
        with pytest.raises(ValueError, match="unknown lock provider type"):
            build_lock_provider({"type": "redis"})

    def test_custom_provider_can_be_a_minimal_subclass(self):
        from backend.observability.deployment_governance_scheduler_locks import (
            LockProvider,
        )

        class _CountingProvider(LockProvider):
            def __init__(self):
                self.writes = 0
                self._locks = {}

            def read(self, job_id):
                return self._locks.get(job_id)

            def write(self, lock):
                self.writes += 1
                self._locks[lock.job_id] = lock

            def delete(self, job_id):
                self._locks.pop(job_id, None)

            def list(self):
                return tuple(self._locks.values())

            def config(self):
                return {"type": "counting"}

        provider = _CountingProvider()
        manager = GovernanceSchedulerLockManager(
            clock=_clock, provider=provider,
        )

        manager.acquire("job-1", "node-1")

        assert provider.writes == 1


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_acquire_publishes_lock_acquired(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceSchedulerLockManager(clock=_clock, event_bus=bus)
        manager.acquire("job-1", "node-1")

        assert received == ["lock_acquired"]

    def test_contention_publishes_lock_contention(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceSchedulerLockManager(clock=_clock, event_bus=bus)
        manager.acquire("job-1", "node-1")
        received.clear()

        manager.acquire("job-1", "node-2")

        assert received == ["lock_contention"]

    def test_release_publishes_lock_released(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceSchedulerLockManager(clock=_clock, event_bus=bus)
        manager.acquire("job-1", "node-1")
        received.clear()

        manager.release("job-1", "node-1")

        assert received == ["lock_released"]

    def test_renew_publishes_lock_renewed(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        manager = GovernanceSchedulerLockManager(clock=_clock, event_bus=bus)
        manager.acquire("job-1", "node-1")
        received.clear()

        manager.renew("job-1", "node-1")

        assert received == ["lock_renewed"]


# --- Scheduler integration -------------------------------------------


class TestSchedulerIntegration:

    def _build(self, clock):
        from backend.observability.deployment_governance_execution_manager import (
            GovernanceExecutionManager,
        )
        from backend.observability.deployment_governance_job_registry import (
            GovernanceJobRegistry,
        )
        from backend.observability.deployment_governance_scheduler import (
            GovernanceScheduler,
        )
        from backend.observability.deployment_governance_trigger_engine import (
            GovernanceTriggerEngine,
        )

        job_registry = GovernanceJobRegistry(clock=clock)
        trigger_engine = GovernanceTriggerEngine(
            clock=clock, job_registry=job_registry,
        )
        scheduler = GovernanceScheduler(
            clock=clock, job_registry=job_registry,
            trigger_engine=trigger_engine, owner_id="node-1",
        )
        execution_manager = GovernanceExecutionManager(clock=clock)

        return scheduler, execution_manager

    def test_run_due_acquires_and_releases_the_lock(self):
        clock = _ControllableClock(BASE_TIME)
        scheduler, execution_manager = self._build(clock)
        lock_manager = GovernanceSchedulerLockManager(clock=clock)

        scheduler.start()
        job = scheduler.register("a", interval_seconds=60)
        clock.now = BASE_TIME + timedelta(seconds=61)

        results = scheduler.run_due(
            execution_manager, lock_manager=lock_manager,
        )

        assert len(results) == 1
        assert results[0].status == "SUCCEEDED"
        # Released again immediately after synchronous dispatch.
        assert lock_manager.is_locked(job.job_id) is False

    def test_run_due_skips_jobs_locked_by_another_node(self):
        clock = _ControllableClock(BASE_TIME)
        scheduler, execution_manager = self._build(clock)
        lock_manager = GovernanceSchedulerLockManager(clock=clock)

        scheduler.start()
        job = scheduler.register("a", interval_seconds=60)
        clock.now = BASE_TIME + timedelta(seconds=61)

        lock_manager.acquire(job.job_id, "node-2", lease_seconds=3600)

        results = scheduler.run_due(
            execution_manager, lock_manager=lock_manager,
        )

        assert results == ()

    def test_run_due_without_lock_manager_is_unaffected(self):
        clock = _ControllableClock(BASE_TIME)
        scheduler, execution_manager = self._build(clock)

        scheduler.start()
        scheduler.register("a", interval_seconds=60)
        clock.now = BASE_TIME + timedelta(seconds=61)

        results = scheduler.run_due(execution_manager)

        assert len(results) == 1


# --- Persistence (provider config only) -------------------------------


class TestPersistenceProviderConfig:

    def test_save_records_provider_config(self, tmp_path):
        from backend.observability.deployment_governance_job_persistence import (
            GovernanceJobPersistence,
        )

        snapshot_path = tmp_path / "snapshot.json"
        lock_manager = GovernanceSchedulerLockManager(
            clock=_clock,
            provider=FileLockProvider(path=tmp_path / "locks.json"),
        )

        GovernanceJobPersistence(
            clock=_clock, lock_manager=lock_manager, path=snapshot_path,
        ).save()

        import json
        document = json.loads(snapshot_path.read_text())

        assert document["lock_provider_config"] == {
            "type": "file", "path": str(tmp_path / "locks.json"),
        }

    def test_load_does_not_restore_active_lock_state(self, tmp_path):
        from backend.observability.deployment_governance_job_persistence import (
            GovernanceJobPersistence,
        )

        snapshot_path = tmp_path / "snapshot.json"
        source_lock_manager = GovernanceSchedulerLockManager(clock=_clock)
        source_lock_manager.acquire("job-1", "node-1")

        GovernanceJobPersistence(
            clock=_clock, lock_manager=source_lock_manager,
            path=snapshot_path,
        ).save()

        target_lock_manager = GovernanceSchedulerLockManager(clock=_clock)
        result = GovernanceJobPersistence(
            clock=_clock, lock_manager=target_lock_manager,
            path=snapshot_path,
        ).load()

        assert result.success is True
        # Active lock state is never restored, by design.
        assert target_lock_manager.list() == ()


# --- Singleton -------------------------------------------------------------


class TestSchedulerLockManagerSingleton:

    def test_get_scheduler_lock_manager_returns_same_instance(self):
        from backend.observability.deployment_governance_scheduler_locks import (
            get_scheduler_lock_manager,
        )

        assert (
            get_scheduler_lock_manager() is get_scheduler_lock_manager()
        )


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceSchedulerLocksApi:

    def test_get_locks_returns_empty_list_initially(self, client) -> None:
        response = client.get("/governance/locks")

        assert response.status_code == 200
        assert response.json() == []

    def test_post_acquire_grants_a_lock(self, client) -> None:
        response = client.post(
            "/governance/locks/job-1/acquire",
            params={"owner_id": "node-1"},
        )

        assert response.status_code == 200

        payload = response.json()
        assert payload["acquired"] is True
        assert payload["owner_id"] == "node-1"

    def test_post_acquire_reports_contention(self, client) -> None:
        client.post(
            "/governance/locks/job-1/acquire",
            params={"owner_id": "node-1"},
        )

        response = client.post(
            "/governance/locks/job-1/acquire",
            params={"owner_id": "node-2"},
        )

        assert response.status_code == 200
        assert response.json()["acquired"] is False

    def test_get_lock_by_job_id(self, client) -> None:
        client.post(
            "/governance/locks/job-1/acquire",
            params={"owner_id": "node-1"},
        )

        response = client.get("/governance/locks/job-1")

        assert response.status_code == 200
        assert response.json()["owner_id"] == "node-1"

    def test_get_unknown_lock_returns_404(self, client) -> None:
        response = client.get("/governance/locks/ghost")

        assert response.status_code == 404

    def test_post_release_releases_the_lock(self, client) -> None:
        client.post(
            "/governance/locks/job-1/acquire",
            params={"owner_id": "node-1"},
        )

        response = client.post(
            "/governance/locks/job-1/release",
            params={"owner_id": "node-1"},
        )

        assert response.status_code == 200
        assert response.json() == {"released": True}

    def test_post_release_by_wrong_owner_is_a_no_op(self, client) -> None:
        client.post(
            "/governance/locks/job-1/acquire",
            params={"owner_id": "node-1"},
        )

        response = client.post(
            "/governance/locks/job-1/release",
            params={"owner_id": "node-2"},
        )

        assert response.status_code == 200
        assert response.json() == {"released": False}

    def test_post_cleanup_returns_removed_count(self, client) -> None:
        response = client.post("/governance/locks/cleanup")

        assert response.status_code == 200
        assert response.json() == {"removed": 0}
