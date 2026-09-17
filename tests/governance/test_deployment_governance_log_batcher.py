import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import Mock

import pytest

from backend.observability.deployment_governance_logging import (
    GovernanceIntegrityLogger,
    GovernanceLogEntry,
)
from backend.observability.deployment_governance_log_repository import (
    InMemoryGovernanceLogRepository,
    SQLiteGovernanceLogRepository,
)
from backend.observability.deployment_governance_log_batcher import (
    GovernanceLogBatch,
    GovernanceLogBatcher,
)
from backend.observability.deployment_governance_delivery_runtime import (
    GovernanceIntegrityDeliveryRuntime,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_flush,
    run_deployment_governance_logging_pending,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(*, event: str = "event") -> GovernanceLogEntry:
    return GovernanceLogEntry(
        timestamp=BASE_TIME,
        level="INFO",
        component="metrics",
        event=event,
        fields={},
    )


class TestGovernanceLogBatch:

    def test_rejects_naive_created_at(self):
        with pytest.raises(ValueError):
            GovernanceLogBatch(
                entries=(), created_at=datetime(2026, 1, 1)
            )


class TestGovernanceLogBatcherConstruction:

    def test_rejects_non_positive_batch_size(self):
        with pytest.raises(ValueError):
            GovernanceLogBatcher(
                InMemoryGovernanceLogRepository(), batch_size=0
            )

    def test_rejects_non_positive_flush_interval(self):
        with pytest.raises(ValueError):
            GovernanceLogBatcher(
                InMemoryGovernanceLogRepository(),
                flush_interval_seconds=0,
            )


class TestGovernanceLogBatcherEnqueue:

    def test_enqueue_increments_pending_count(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=10, clock=lambda: BASE_TIME
        )

        batcher.enqueue(_entry())
        batcher.enqueue(_entry())

        assert batcher.pending_count() == 2
        assert repository.list() == ()

    def test_enqueue_does_not_write_to_repository(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=10, clock=lambda: BASE_TIME
        )

        batcher.enqueue(_entry())

        assert repository.list() == ()


class TestGovernanceLogBatcherSizeTriggeredFlush:

    def test_flush_if_needed_triggers_at_batch_size(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=3, clock=lambda: BASE_TIME
        )

        batcher.enqueue(_entry(event="a"))
        batcher.enqueue(_entry(event="b"))

        assert batcher.flush_if_needed() is None
        assert repository.list() == ()

        batcher.enqueue(_entry(event="c"))

        batch = batcher.flush_if_needed()

        assert batch is not None
        assert len(batch.entries) == 3
        assert len(repository.list()) == 3
        assert batcher.pending_count() == 0

    def test_flush_if_needed_below_threshold_is_a_no_op(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=10, clock=lambda: BASE_TIME
        )

        batcher.enqueue(_entry())

        assert batcher.flush_if_needed() is None
        assert batcher.pending_count() == 1


class TestGovernanceLogBatcherIntervalTriggeredFlush:

    def test_flush_if_needed_triggers_after_interval_elapses(self):
        repository = InMemoryGovernanceLogRepository()

        current_time = BASE_TIME

        def _clock():
            return current_time

        batcher = GovernanceLogBatcher(
            repository,
            batch_size=1000,
            flush_interval_seconds=5.0,
            clock=_clock,
        )

        batcher.enqueue(_entry())

        assert batcher.flush_if_needed() is None

        current_time = BASE_TIME + timedelta(seconds=6)

        batch = batcher.flush_if_needed()

        assert batch is not None
        assert len(batch.entries) == 1

    def test_flush_if_needed_before_interval_elapses_is_a_no_op(
        self,
    ):
        repository = InMemoryGovernanceLogRepository()

        current_time = BASE_TIME

        def _clock():
            return current_time

        batcher = GovernanceLogBatcher(
            repository,
            batch_size=1000,
            flush_interval_seconds=5.0,
            clock=_clock,
        )

        batcher.enqueue(_entry())

        current_time = BASE_TIME + timedelta(seconds=2)

        assert batcher.flush_if_needed() is None


class TestGovernanceLogBatcherShutdownFlush:

    def test_flush_writes_pending_entries_unconditionally(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=1000, clock=lambda: BASE_TIME
        )

        batcher.enqueue(_entry())
        batcher.enqueue(_entry())

        batch = batcher.flush()

        assert batch is not None
        assert len(batch.entries) == 2
        assert len(repository.list()) == 2

    def test_flush_on_empty_queue_returns_none(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, clock=lambda: BASE_TIME
        )

        assert batcher.flush() is None
        assert repository.list() == ()

    def test_flush_resets_pending_count(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, clock=lambda: BASE_TIME
        )

        batcher.enqueue(_entry())

        batcher.flush()

        assert batcher.pending_count() == 0


class TestGovernanceLogBatcherOrdering:

    def test_flush_preserves_insertion_order(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=1000, clock=lambda: BASE_TIME
        )

        for i in range(10):
            batcher.enqueue(_entry(event=f"event_{i}"))

        batch = batcher.flush()

        assert [e.event for e in batch.entries] == [
            f"event_{i}" for i in range(10)
        ]
        assert [e.event for e in repository.list()] == [
            f"event_{i}" for i in range(10)
        ]

    def test_multiple_flushes_preserve_order_across_batches(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=1000, clock=lambda: BASE_TIME
        )

        batcher.enqueue(_entry(event="first"))
        batcher.flush()

        batcher.enqueue(_entry(event="second"))
        batcher.flush()

        assert [e.event for e in repository.list()] == [
            "first",
            "second",
        ]


class TestGovernanceLogBatcherThreadSafety:

    def test_concurrent_enqueue_never_loses_entries(self):
        import threading

        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=100000, clock=lambda: BASE_TIME
        )

        def _enqueue_many():
            for _ in range(200):
                batcher.enqueue(_entry())

        threads = [
            threading.Thread(target=_enqueue_many) for _ in range(4)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert batcher.pending_count() == 800

        batch = batcher.flush()

        assert len(batch.entries) == 800


class TestGovernanceLogRepositoryAppendMany:

    def test_in_memory_append_many_preserves_order(self):
        repository = InMemoryGovernanceLogRepository()

        entries = [_entry(event=f"e{i}") for i in range(5)]

        repository.append_many(entries)

        assert [e.event for e in repository.list()] == [
            f"e{i}" for i in range(5)
        ]

    def test_in_memory_append_many_empty_is_a_no_op(self):
        repository = InMemoryGovernanceLogRepository()

        repository.append_many([])

        assert repository.list() == ()

    def test_in_memory_append_many_triggers_rotation_once(self):
        repository = InMemoryGovernanceLogRepository()

        calls = []

        class _FakeRotationService:
            def rotate(self):
                calls.append(1)

        repository.set_rotation_service(_FakeRotationService())

        repository.append_many([_entry(), _entry(), _entry()])

        assert calls == [1]

    def test_sqlite_append_many_preserves_order(self, tmp_path):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        database = SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=tmp_path / "b.db")
        )

        repository = SQLiteGovernanceLogRepository(database)

        entries = [_entry(event=f"e{i}") for i in range(5)]

        repository.append_many(entries)

        assert [e.event for e in repository.list()] == [
            f"e{i}" for i in range(5)
        ]

    def test_sqlite_append_many_empty_is_a_no_op(self, tmp_path):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        database = SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=tmp_path / "b.db")
        )

        repository = SQLiteGovernanceLogRepository(database)

        repository.append_many([])

        assert repository.list() == ()

    def test_sqlite_append_many_single_transaction(self, tmp_path):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        database = SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=tmp_path / "b.db")
        )

        repository = SQLiteGovernanceLogRepository(database)

        entries = [_entry(event=f"e{i}") for i in range(50)]

        repository.append_many(entries)

        assert len(repository.list()) == 50


class TestGovernanceLogBatcherLoggerIntegration:

    def test_logger_enqueues_instead_of_writing_directly(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=1000, clock=lambda: BASE_TIME
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            batcher=batcher,
        )

        logger.info("metrics", "record_success")

        assert repository.list() == ()
        assert batcher.pending_count() == 1

    def test_logger_triggers_size_based_auto_flush(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=2, clock=lambda: BASE_TIME
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            batcher=batcher,
        )

        logger.info("metrics", "first")
        assert repository.list() == ()

        logger.info("metrics", "second")
        assert len(repository.list()) == 2

    def test_logger_without_batcher_writes_directly(self):
        repository = InMemoryGovernanceLogRepository()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME, repository=repository
        )

        logger.info("metrics", "record_success")

        assert len(repository.list()) == 1

    def test_sampled_out_entry_is_never_enqueued(self):
        from backend.observability.deployment_governance_log_sampling import (
            GovernanceLogSamplingPolicy,
            GovernanceLogSamplingService,
        )

        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=1000, clock=lambda: BASE_TIME
        )

        sampling_service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.0)
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            batcher=batcher,
            sampling_service=sampling_service,
        )

        logger.info("metrics", "routine_event")

        assert batcher.pending_count() == 0

    def test_set_batcher_attaches_after_construction(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=1000, clock=lambda: BASE_TIME
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME, repository=repository
        )

        logger.set_batcher(batcher)

        logger.info("metrics", "record_success")

        assert repository.list() == ()
        assert batcher.pending_count() == 1


class TestGovernanceLogBatcherRuntimeIntegration:

    def test_stop_flushes_pending_entries(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=1000, clock=lambda: BASE_TIME
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            batcher=batcher,
        )

        logger.info("metrics", "buffered_before_shutdown")

        assert repository.list() == ()

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = BASE_TIME

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            logger=logger,
            batcher=batcher,
        )

        runtime.start()
        runtime.stop()

        events = [e.event for e in repository.list()]

        assert "buffered_before_shutdown" in events

    def test_run_iteration_checks_flush_if_needed(self):
        repository = InMemoryGovernanceLogRepository()

        current_time = BASE_TIME

        def _clock_fn():
            return current_time

        batcher = GovernanceLogBatcher(
            repository,
            batch_size=1000,
            flush_interval_seconds=5.0,
            clock=_clock_fn,
        )

        logger = GovernanceIntegrityLogger(
            clock=_clock_fn,
            repository=repository,
            batcher=batcher,
        )

        logger.info("metrics", "queued_event")

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []

        class _Clock:
            def now(self):
                return current_time

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=_Clock(),
            logger=logger,
            batcher=batcher,
        )

        runtime.start()

        # Not enough time has passed yet.
        runtime.run_iteration()

        assert repository.list() == ()

        current_time = BASE_TIME + timedelta(seconds=6)

        runtime.run_iteration()

        # "queued_event" plus the runtime's own "runtime_started"
        # log call from start() -- both went through the same
        # attached batcher.
        events = [e.event for e in repository.list()]

        assert "queued_event" in events
        assert "runtime_started" in events

    def test_runtime_without_batcher_still_works(self):
        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        provider_registry.list_providers.return_value = []
        clock = Mock()
        clock.now.return_value = BASE_TIME

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
        )

        runtime.start()
        runtime.run_iteration()
        runtime.stop()


class TestGovernanceLogBatcherCli:

    def _stub_runtime(self, batcher):
        class _StubRuntime:
            def build_integrity_log_batcher(self):
                return batcher

        return _StubRuntime()

    def test_pending_runner_reports_count(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=1000, clock=lambda: BASE_TIME
        )

        batcher.enqueue(_entry())
        batcher.enqueue(_entry())

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(batcher),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_pending(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["pending"] == 2

    def test_flush_runner_writes_and_reports_count(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=1000, clock=lambda: BASE_TIME
        )

        batcher.enqueue(_entry())
        batcher.enqueue(_entry())
        batcher.enqueue(_entry())

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(batcher),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_flush(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["flushed"] == 3
        assert len(repository.list()) == 3
        assert batcher.pending_count() == 0

    def test_flush_runner_handles_nothing_pending(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, clock=lambda: BASE_TIME
        )

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(batcher),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_flush(
            stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0
        assert "Flushed 0" in stdout.getvalue()
