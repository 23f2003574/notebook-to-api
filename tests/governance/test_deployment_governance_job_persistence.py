from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_execution_manager import (
    GovernanceExecutionManager,
)
from backend.observability.deployment_governance_job_persistence import (
    CURRENT_SCHEMA_VERSION,
    GovernanceJobPersistence,
    PersistenceResult,
    PersistenceSnapshot,
)
from backend.observability.deployment_governance_job_registry import (
    GovernanceJobRegistry,
)
from backend.observability.deployment_governance_retry import (
    GovernanceRetryEngine,
)
from backend.observability.deployment_governance_scheduler import (
    GovernanceScheduler,
)
from backend.observability.deployment_governance_trigger_engine import (
    GovernanceTriggerEngine,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _clock():
    return BASE_TIME


def _build_components(clock=_clock):
    job_registry = GovernanceJobRegistry(clock=clock)
    trigger_engine = GovernanceTriggerEngine(
        clock=clock, job_registry=job_registry
    )
    retry_engine = GovernanceRetryEngine(clock=clock)
    scheduler = GovernanceScheduler(
        clock=clock, job_registry=job_registry,
        trigger_engine=trigger_engine,
    )
    execution_manager = GovernanceExecutionManager(clock=clock)

    return job_registry, trigger_engine, retry_engine, scheduler, execution_manager


@pytest.fixture(autouse=True)
def _reset_singletons():
    """
    The job persistence layer and every component it wraps are
    process-wide singletons. Most tests below construct their own
    fresh set of components instead (matching every other test file in
    this series); only the API tests touch the shared singletons, so
    only those need resetting.
    """

    from backend.observability.deployment_governance_job_persistence import (
        get_job_persistence,
    )
    from backend.observability.deployment_governance_job_registry import (
        get_job_registry,
    )
    from backend.observability.deployment_governance_lifecycle import (
        get_lifecycle_manager,
    )
    from backend.observability.deployment_governance_retry import (
        get_retry_engine,
    )
    from backend.observability.deployment_governance_trigger_engine import (
        get_trigger_engine,
    )

    def _reset():
        get_lifecycle_manager().shutdown()
        get_job_persistence().clear()

        retry_engine = get_retry_engine()

        for attempt in retry_engine.pending():
            retry_engine.cancel_retry(attempt.execution_id)

        get_trigger_engine().clear()
        get_job_registry().clear()

    _reset()
    yield
    _reset()


# --- Models --------------------------------------------------------------


class TestPersistenceSnapshot:

    def test_rejects_negative_version(self):
        with pytest.raises(ValueError, match="version must be >= 0"):
            PersistenceSnapshot(
                version=-1, created_at=BASE_TIME, jobs=0, triggers=0,
                pending_retries=0,
            )

    def test_rejects_naive_created_at(self):
        with pytest.raises(
            ValueError, match="created_at must be timezone-aware"
        ):
            PersistenceSnapshot(
                version=1, created_at=datetime(2026, 7, 21, 12, 0, 0),
                jobs=0, triggers=0, pending_retries=0,
            )

    def test_rejects_negative_counts(self):
        with pytest.raises(ValueError, match="jobs must be >= 0"):
            PersistenceSnapshot(
                version=1, created_at=BASE_TIME, jobs=-1, triggers=0,
                pending_retries=0,
            )

    def test_to_dict(self):
        snapshot = PersistenceSnapshot(
            version=1, created_at=BASE_TIME, jobs=2, triggers=1,
            pending_retries=0,
        )

        assert snapshot.to_dict() == {
            "version": 1,
            "created_at": BASE_TIME.isoformat(),
            "jobs": 2,
            "triggers": 1,
            "pending_retries": 0,
        }


class TestPersistenceResult:

    def test_rejects_empty_operation(self):
        with pytest.raises(ValueError, match="operation must not be empty"):
            PersistenceResult(
                success=True, operation="", duration_ms=0, message=None,
            )

    def test_rejects_negative_duration(self):
        with pytest.raises(ValueError, match="duration_ms must be >= 0"):
            PersistenceResult(
                success=True, operation="save", duration_ms=-1,
                message=None,
            )

    def test_to_dict(self):
        result = PersistenceResult(
            success=False, operation="load", duration_ms=5,
            message="boom",
        )

        assert result.to_dict() == {
            "success": False,
            "operation": "load",
            "duration_ms": 5,
            "message": "boom",
        }


# --- Save snapshot -----------------------------------------------------


class TestSaveSnapshot:

    def test_save_reports_success(self):
        job_registry, trigger_engine, retry_engine, scheduler, _ = (
            _build_components()
        )
        job_registry.register("job-1", "a")

        persistence = GovernanceJobPersistence(
            clock=_clock, job_registry=job_registry,
            trigger_engine=trigger_engine, retry_engine=retry_engine,
            scheduler=scheduler,
        )

        result = persistence.save()

        assert result.success is True
        assert result.operation == "save"

    def test_snapshot_reflects_saved_counts(self):
        job_registry, trigger_engine, retry_engine, scheduler, _ = (
            _build_components()
        )
        job_registry.register("job-1", "a")
        job_registry.register("job-2", "b")

        persistence = GovernanceJobPersistence(
            clock=_clock, job_registry=job_registry,
            trigger_engine=trigger_engine, retry_engine=retry_engine,
            scheduler=scheduler,
        )
        persistence.save()

        snapshot = persistence.snapshot()

        assert snapshot.version == CURRENT_SCHEMA_VERSION
        assert snapshot.jobs == 2

    def test_save_is_atomic_to_a_file(self, tmp_path: Path):
        job_registry, trigger_engine, retry_engine, scheduler, _ = (
            _build_components()
        )
        job_registry.register("job-1", "a")

        path = tmp_path / "snapshot.json"
        persistence = GovernanceJobPersistence(
            clock=_clock, job_registry=job_registry,
            trigger_engine=trigger_engine, retry_engine=retry_engine,
            scheduler=scheduler, path=path,
        )
        persistence.save()

        assert path.exists()
        assert not path.with_name(path.name + ".tmp").exists()

        document = json.loads(path.read_text())
        assert len(document["jobs"]) == 1

    def test_save_serializes_deterministically(self, tmp_path: Path):
        job_registry, trigger_engine, retry_engine, scheduler, _ = (
            _build_components()
        )
        job_registry.register("job-1", "a")

        path = tmp_path / "snapshot.json"
        persistence = GovernanceJobPersistence(
            clock=_clock, job_registry=job_registry,
            trigger_engine=trigger_engine, retry_engine=retry_engine,
            scheduler=scheduler, path=path,
        )
        persistence.save()
        first = path.read_text()

        persistence.save()
        second = path.read_text()

        assert first == second


# --- Load snapshot -----------------------------------------------------


class TestLoadSnapshot:

    def test_load_restores_jobs_via_shared_file(self, tmp_path: Path):
        path = tmp_path / "snapshot.json"

        source_registry = GovernanceJobRegistry(clock=_clock)
        source_registry.register("job-1", "a", namespace="ns")
        GovernanceJobPersistence(
            clock=_clock, job_registry=source_registry, path=path,
        ).save()

        target_registry = GovernanceJobRegistry(clock=_clock)
        result = GovernanceJobPersistence(
            clock=_clock, job_registry=target_registry, path=path,
        ).load()

        assert result.success is True
        assert target_registry.exists("job-1") is True
        assert target_registry.get("job-1").namespace == "ns"

    def test_load_skips_already_existing_jobs(self, tmp_path: Path):
        path = tmp_path / "snapshot.json"

        source_registry = GovernanceJobRegistry(clock=_clock)
        source_registry.register("job-1", "a")
        GovernanceJobPersistence(
            clock=_clock, job_registry=source_registry, path=path,
        ).save()

        target_registry = GovernanceJobRegistry(clock=_clock)
        target_registry.register("job-1", "different-name")

        GovernanceJobPersistence(
            clock=_clock, job_registry=target_registry, path=path,
        ).load()

        # Left alone, not overwritten.
        assert target_registry.get("job-1").name == "different-name"

    def test_load_starts_the_scheduler_if_it_was_running(
        self, tmp_path: Path,
    ):
        path = tmp_path / "snapshot.json"

        _, _, _, source_scheduler, _ = _build_components()
        source_scheduler.start()
        GovernanceJobPersistence(
            clock=_clock, scheduler=source_scheduler, path=path,
        ).save()

        _, _, _, target_scheduler, _ = _build_components()
        GovernanceJobPersistence(
            clock=_clock, scheduler=target_scheduler, path=path,
        ).load()

        assert target_scheduler.status().running is True

    def test_load_does_not_start_scheduler_if_it_was_stopped(
        self, tmp_path: Path,
    ):
        path = tmp_path / "snapshot.json"

        _, _, _, source_scheduler, _ = _build_components()
        GovernanceJobPersistence(
            clock=_clock, scheduler=source_scheduler, path=path,
        ).save()

        _, _, _, target_scheduler, _ = _build_components()
        GovernanceJobPersistence(
            clock=_clock, scheduler=target_scheduler, path=path,
        ).load()

        assert target_scheduler.status().running is False


# --- Empty snapshot ------------------------------------------------------


class TestEmptySnapshot:

    def test_load_with_nothing_saved_succeeds(self):
        job_registry, trigger_engine, retry_engine, scheduler, _ = (
            _build_components()
        )
        persistence = GovernanceJobPersistence(
            clock=_clock, job_registry=job_registry,
            trigger_engine=trigger_engine, retry_engine=retry_engine,
            scheduler=scheduler,
        )

        result = persistence.load()

        assert result.success is True
        assert "no snapshot found" in result.message

    def test_snapshot_with_nothing_saved_is_a_zeroed_placeholder(self):
        persistence = GovernanceJobPersistence(clock=_clock)

        snapshot = persistence.snapshot()

        assert snapshot.version == 0
        assert snapshot.jobs == 0
        assert snapshot.triggers == 0
        assert snapshot.pending_retries == 0

    def test_clear_makes_a_saved_snapshot_disappear(self):
        job_registry, trigger_engine, retry_engine, scheduler, _ = (
            _build_components()
        )
        job_registry.register("job-1", "a")

        persistence = GovernanceJobPersistence(
            clock=_clock, job_registry=job_registry,
            trigger_engine=trigger_engine, retry_engine=retry_engine,
            scheduler=scheduler,
        )
        persistence.save()

        persistence.clear()

        assert persistence.snapshot().version == 0


# --- Corrupted snapshot recovery -------------------------------------


class TestCorruptedSnapshotRecovery:

    def test_invalid_json_is_handled_gracefully(self, tmp_path: Path):
        path = tmp_path / "snapshot.json"
        path.write_text("not valid json {{{")

        persistence = GovernanceJobPersistence(clock=_clock, path=path)

        result = persistence.load()

        assert result.success is False
        assert "corrupted snapshot" in result.message

    def test_missing_required_field_is_handled_gracefully(
        self, tmp_path: Path,
    ):
        path = tmp_path / "snapshot.json"
        path.write_text(json.dumps({"version": 1}))

        persistence = GovernanceJobPersistence(clock=_clock, path=path)

        result = persistence.load()

        assert result.success is False
        assert "corrupted snapshot" in result.message

    def test_corrupted_snapshot_does_not_raise(self, tmp_path: Path):
        path = tmp_path / "snapshot.json"
        path.write_text("{{{ not json")

        persistence = GovernanceJobPersistence(clock=_clock, path=path)

        # Should not raise.
        persistence.load()

    def test_corrupted_snapshot_restores_nothing(self, tmp_path: Path):
        path = tmp_path / "snapshot.json"
        path.write_text("not valid json {{{")

        job_registry = GovernanceJobRegistry(clock=_clock)
        persistence = GovernanceJobPersistence(
            clock=_clock, job_registry=job_registry, path=path,
        )

        persistence.load()

        assert job_registry.list() == ()


# --- Schema version compatibility -----------------------------------


class TestSchemaVersionCompatibility:

    def test_future_version_is_rejected_gracefully(self, tmp_path: Path):
        path = tmp_path / "snapshot.json"
        path.write_text(
            json.dumps(
                {
                    "version": CURRENT_SCHEMA_VERSION + 1,
                    "created_at": BASE_TIME.isoformat(),
                    "jobs": [],
                    "triggers": [],
                    "pending_retries": [],
                }
            )
        )

        persistence = GovernanceJobPersistence(clock=_clock, path=path)

        result = persistence.load()

        assert result.success is False
        assert "unsupported snapshot schema version" in result.message

    def test_current_version_loads_successfully(self, tmp_path: Path):
        path = tmp_path / "snapshot.json"
        path.write_text(
            json.dumps(
                {
                    "version": CURRENT_SCHEMA_VERSION,
                    "created_at": BASE_TIME.isoformat(),
                    "jobs": [],
                    "triggers": [],
                    "pending_retries": [],
                }
            )
        )

        persistence = GovernanceJobPersistence(clock=_clock, path=path)

        result = persistence.load()

        assert result.success is True


# --- Retry queue restoration -----------------------------------------


class TestRetryQueueRestoration:

    def test_pending_retry_round_trips_via_file(self, tmp_path: Path):
        path = tmp_path / "snapshot.json"

        source_retry_engine = GovernanceRetryEngine(clock=_clock)
        source_retry_engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        source_retry_engine.schedule_retry(
            "exec-1", "p", job_id="job-1", reason="boom",
        )

        GovernanceJobPersistence(
            clock=_clock, retry_engine=source_retry_engine, path=path,
        ).save()

        target_retry_engine = GovernanceRetryEngine(clock=_clock)
        target_retry_engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )

        result = GovernanceJobPersistence(
            clock=_clock, retry_engine=target_retry_engine, path=path,
        ).load()

        assert result.success is True

        restored = target_retry_engine.pending()
        assert len(restored) == 1
        assert restored[0].execution_id == "exec-1"
        assert restored[0].reason == "boom"

    def test_pending_context_returns_none_when_not_pending(self):
        engine = GovernanceRetryEngine(clock=_clock)

        assert engine.pending_context("ghost") is None

    def test_pending_context_returns_job_id_and_policy_id(self):
        engine = GovernanceRetryEngine(clock=_clock)
        engine.register_policy(
            "p", max_attempts=3, strategy="fixed",
            base_delay_seconds=5, max_delay_seconds=100,
        )
        engine.schedule_retry("exec-1", "p", job_id="job-1")

        assert engine.pending_context("exec-1") == ("job-1", "p")

    def test_save_retry_queue_persists_only_that_section(
        self, tmp_path: Path,
    ):
        path = tmp_path / "snapshot.json"

        job_registry = GovernanceJobRegistry(clock=_clock)
        job_registry.register("job-1", "a")
        retry_engine = GovernanceRetryEngine(clock=_clock)

        persistence = GovernanceJobPersistence(
            clock=_clock, job_registry=job_registry,
            retry_engine=retry_engine, path=path,
        )
        persistence.save_jobs()
        persistence.save_retry_queue()

        document = json.loads(path.read_text())

        assert len(document["jobs"]) == 1
        assert document["pending_retries"] == []


# --- Scheduler restoration -------------------------------------------


class TestSchedulerRestoration:

    def test_scheduler_running_flag_is_captured(self, tmp_path: Path):
        path = tmp_path / "snapshot.json"

        _, _, _, scheduler, _ = _build_components()
        scheduler.start()

        GovernanceJobPersistence(
            clock=_clock, scheduler=scheduler, path=path,
        ).save()

        document = json.loads(path.read_text())

        assert document["scheduler_running"] is True


# --- Event publication ---------------------------------------------------


class TestEventPublication:

    def test_save_publishes_persistence_saved_and_snapshot_created(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        persistence = GovernanceJobPersistence(clock=_clock, event_bus=bus)
        persistence.save()

        assert received == ["persistence_saved", "snapshot_created"]

    def test_load_publishes_persistence_loaded(self):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        persistence = GovernanceJobPersistence(clock=_clock, event_bus=bus)
        persistence.load()

        assert received == ["persistence_loaded"]

    def test_corrupted_load_publishes_persistence_failed(
        self, tmp_path: Path,
    ):
        from backend.observability.deployment_governance_event_bus import (
            GovernanceEventBus,
        )

        path = tmp_path / "snapshot.json"
        path.write_text("not json {{{")

        bus = GovernanceEventBus()
        received = []
        bus.subscribe_all(lambda e: received.append(e.event_type))

        persistence = GovernanceJobPersistence(
            clock=_clock, event_bus=bus, path=path,
        )
        persistence.load()

        assert received == ["persistence_failed"]


# --- Singleton -------------------------------------------------------------


class TestJobPersistenceSingleton:

    def test_get_job_persistence_returns_same_instance(self):
        from backend.observability.deployment_governance_job_persistence import (
            get_job_persistence,
        )

        assert get_job_persistence() is get_job_persistence()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceJobPersistenceApi:

    def test_get_persistence_returns_a_snapshot_summary(
        self, client
    ) -> None:
        response = client.get("/governance/persistence")

        assert response.status_code == 200

        payload = response.json()
        assert "version" in payload
        assert "jobs" in payload

    def test_post_save_then_get_reflects_saved_state(self, client) -> None:
        from backend.observability.deployment_governance_job_registry import (
            get_job_registry,
        )

        get_job_registry().register("job-1", "a")

        save_response = client.post("/governance/persistence/save")
        assert save_response.status_code == 200
        assert save_response.json()["success"] is True

        response = client.get("/governance/persistence")
        assert response.json()["jobs"] == 1

    def test_post_load_returns_success(self, client) -> None:
        client.post("/governance/persistence/save")

        response = client.post("/governance/persistence/load")

        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_delete_persistence_clears_the_snapshot(self, client) -> None:
        client.post("/governance/persistence/save")

        response = client.delete("/governance/persistence")

        assert response.status_code == 200
        assert response.json() == {"cleared": True}

        follow_up = client.get("/governance/persistence")
        assert follow_up.json()["version"] == 0
