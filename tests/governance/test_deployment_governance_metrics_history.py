from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetrics,
)
from backend.observability.deployment_governance_metrics_history import (
    GovernanceIntegrityMetricsSnapshot,
    InMemoryGovernanceIntegrityMetricsHistoryRepository,
    SQLiteGovernanceIntegrityMetricsHistoryRepository,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _metrics(**overrides) -> GovernanceIntegrityMetrics:
    fields = {
        "total_dispatches": 2,
        "successful_dispatches": 1,
        "failed_dispatches": 1,
        "retry_dispatches": 0,
        "average_duration_ms": 50.0,
    }

    fields.update(overrides)

    return GovernanceIntegrityMetrics(**fields)


def _snapshot(
    *, offset_minutes: int = 0, **metrics_overrides
) -> GovernanceIntegrityMetricsSnapshot:
    return GovernanceIntegrityMetricsSnapshot(
        captured_at=BASE_TIME + timedelta(minutes=offset_minutes),
        metrics=_metrics(**metrics_overrides),
    )


class TestGovernanceIntegrityMetricsSnapshot:

    def test_rejects_naive_captured_at(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsSnapshot(
                captured_at=datetime(2026, 1, 1),
                metrics=_metrics(),
            )

    def test_to_dict(self):
        snapshot = _snapshot()

        payload = snapshot.to_dict()

        assert payload["captured_at"] == BASE_TIME.isoformat()
        assert payload["metrics"] == snapshot.metrics.to_dict()


class TestInMemoryGovernanceIntegrityMetricsHistoryRepository:

    def test_latest_is_none_when_empty(self):
        repository = (
            InMemoryGovernanceIntegrityMetricsHistoryRepository()
        )

        assert repository.latest() is None

    def test_append_then_latest(self):
        repository = (
            InMemoryGovernanceIntegrityMetricsHistoryRepository()
        )

        first = _snapshot(offset_minutes=0)
        second = _snapshot(offset_minutes=1)

        repository.append(first)
        repository.append(second)

        assert repository.latest() == second

    def test_list_returns_newest_first(self):
        repository = (
            InMemoryGovernanceIntegrityMetricsHistoryRepository()
        )

        first = _snapshot(offset_minutes=0)
        second = _snapshot(offset_minutes=1)
        third = _snapshot(offset_minutes=2)

        repository.append(first)
        repository.append(second)
        repository.append(third)

        assert repository.list() == (third, second, first)

    def test_list_respects_limit(self):
        repository = (
            InMemoryGovernanceIntegrityMetricsHistoryRepository()
        )

        for offset in range(5):
            repository.append(_snapshot(offset_minutes=offset))

        limited = repository.list(limit=2)

        assert len(limited) == 2
        assert limited[0].captured_at == BASE_TIME + timedelta(
            minutes=4
        )
        assert limited[1].captured_at == BASE_TIME + timedelta(
            minutes=3
        )

    def test_prune_keeps_only_newest_entries(self):
        repository = (
            InMemoryGovernanceIntegrityMetricsHistoryRepository()
        )

        for offset in range(5):
            repository.append(_snapshot(offset_minutes=offset))

        discarded = repository.prune(2)

        assert discarded == 3
        remaining = repository.list()
        assert len(remaining) == 2
        assert remaining[0].captured_at == BASE_TIME + timedelta(
            minutes=4
        )
        assert remaining[1].captured_at == BASE_TIME + timedelta(
            minutes=3
        )

    def test_prune_is_a_no_op_when_under_limit(self):
        repository = (
            InMemoryGovernanceIntegrityMetricsHistoryRepository()
        )

        repository.append(_snapshot())

        discarded = repository.prune(10)

        assert discarded == 0
        assert len(repository.list()) == 1


class TestSQLiteGovernanceIntegrityMetricsHistoryRepository:

    def _database(self, tmp_path, name="history.db"):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        return SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=tmp_path / name)
        )

    def test_latest_is_none_when_empty(self, tmp_path):
        repository = SQLiteGovernanceIntegrityMetricsHistoryRepository(
            self._database(tmp_path)
        )

        assert repository.latest() is None

    def test_append_then_latest(self, tmp_path):
        repository = SQLiteGovernanceIntegrityMetricsHistoryRepository(
            self._database(tmp_path)
        )

        first = _snapshot(offset_minutes=0)
        second = _snapshot(offset_minutes=1)

        repository.append(first)
        repository.append(second)

        assert repository.latest() == second

    def test_list_returns_newest_first(self, tmp_path):
        repository = SQLiteGovernanceIntegrityMetricsHistoryRepository(
            self._database(tmp_path)
        )

        first = _snapshot(offset_minutes=0)
        second = _snapshot(offset_minutes=1)
        third = _snapshot(offset_minutes=2)

        repository.append(first)
        repository.append(second)
        repository.append(third)

        assert repository.list() == (third, second, first)

    def test_list_respects_limit(self, tmp_path):
        repository = SQLiteGovernanceIntegrityMetricsHistoryRepository(
            self._database(tmp_path)
        )

        for offset in range(5):
            repository.append(_snapshot(offset_minutes=offset))

        limited = repository.list(limit=2)

        assert len(limited) == 2

    def test_prune_keeps_only_newest_entries(self, tmp_path):
        repository = SQLiteGovernanceIntegrityMetricsHistoryRepository(
            self._database(tmp_path)
        )

        for offset in range(5):
            repository.append(_snapshot(offset_minutes=offset))

        discarded = repository.prune(2)

        assert discarded == 3
        assert len(repository.list()) == 2

    def test_persists_and_survives_reload(self, tmp_path):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        database_path = tmp_path / "history.db"

        database = SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=database_path)
        )

        repository = SQLiteGovernanceIntegrityMetricsHistoryRepository(
            database
        )

        snapshot = _snapshot()

        repository.append(snapshot)

        reloaded_database = SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=database_path)
        )

        reloaded_repository = (
            SQLiteGovernanceIntegrityMetricsHistoryRepository(
                reloaded_database
            )
        )

        assert reloaded_repository.latest() == snapshot
