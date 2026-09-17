from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_logging import (
    GovernanceLogEntry,
)
from backend.observability.deployment_governance_log_repository import (
    InMemoryGovernanceLogRepository,
    SQLiteGovernanceLogRepository,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(
    *,
    offset_minutes: int = 0,
    level: str = "INFO",
    component: str = "metrics",
    event: str = "record_success",
    **fields,
) -> GovernanceLogEntry:
    return GovernanceLogEntry(
        timestamp=BASE_TIME + timedelta(minutes=offset_minutes),
        level=level,
        component=component,
        event=event,
        fields=fields,
    )


class TestInMemoryGovernanceLogRepository:

    def test_list_is_empty_initially(self):
        repository = InMemoryGovernanceLogRepository()

        assert repository.list() == ()
        assert repository.tail() == ()

    def test_append_then_list(self):
        repository = InMemoryGovernanceLogRepository()

        entry = _entry()

        repository.append(entry)

        assert repository.list() == (entry,)

    def test_list_returns_oldest_first(self):
        repository = InMemoryGovernanceLogRepository()

        first = _entry(offset_minutes=0, event="first")
        second = _entry(offset_minutes=1, event="second")
        third = _entry(offset_minutes=2, event="third")

        repository.append(first)
        repository.append(second)
        repository.append(third)

        assert repository.list() == (first, second, third)

    def test_tail_returns_newest_first(self):
        repository = InMemoryGovernanceLogRepository()

        first = _entry(offset_minutes=0, event="first")
        second = _entry(offset_minutes=1, event="second")
        third = _entry(offset_minutes=2, event="third")

        repository.append(first)
        repository.append(second)
        repository.append(third)

        assert repository.tail() == (third, second, first)

    def test_tail_respects_limit(self):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(
                _entry(offset_minutes=offset, event=f"event_{offset}")
            )

        limited = repository.tail(limit=2)

        assert len(limited) == 2
        assert limited[0].event == "event_4"
        assert limited[1].event == "event_3"

    def test_list_respects_limit(self):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(
                _entry(offset_minutes=offset, event=f"event_{offset}")
            )

        limited = repository.list(limit=2)

        assert len(limited) == 2
        assert limited[0].event == "event_0"
        assert limited[1].event == "event_1"

    def test_filters_by_level(self):
        repository = InMemoryGovernanceLogRepository()

        info_entry = _entry(level="INFO", event="info_event")
        warning_entry = _entry(
            offset_minutes=1, level="WARNING", event="warning_event"
        )

        repository.append(info_entry)
        repository.append(warning_entry)

        assert repository.list(level="WARNING") == (warning_entry,)
        assert repository.tail(level="WARNING") == (warning_entry,)

    def test_filters_by_component(self):
        repository = InMemoryGovernanceLogRepository()

        metrics_entry = _entry(component="metrics")
        engine_entry = _entry(
            offset_minutes=1, component="delivery_engine"
        )

        repository.append(metrics_entry)
        repository.append(engine_entry)

        assert repository.list(component="delivery_engine") == (
            engine_entry,
        )
        assert repository.tail(component="delivery_engine") == (
            engine_entry,
        )

    def test_filters_by_level_and_component_combined(self):
        repository = InMemoryGovernanceLogRepository()

        match = _entry(
            offset_minutes=2, level="ERROR", component="delivery_engine"
        )

        repository.append(_entry(level="INFO", component="metrics"))
        repository.append(
            _entry(
                offset_minutes=1,
                level="ERROR",
                component="metrics",
            )
        )
        repository.append(match)

        assert (
            repository.list(level="ERROR", component="delivery_engine")
            == (match,)
        )

    def test_rejects_negative_limit(self):
        repository = InMemoryGovernanceLogRepository()

        with pytest.raises(ValueError):
            repository.list(limit=-1)

        with pytest.raises(ValueError):
            repository.tail(limit=-1)

    def test_rejects_invalid_level(self):
        repository = InMemoryGovernanceLogRepository()

        with pytest.raises(ValueError):
            repository.list(level="TRACE")

        with pytest.raises(ValueError):
            repository.tail(level="TRACE")

    def test_clear_empties_repository(self):
        repository = InMemoryGovernanceLogRepository()

        repository.append(_entry())
        repository.clear()

        assert repository.list() == ()
        assert repository.tail() == ()

    def test_prune_discards_oldest_beyond_max_entries(self):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(
                _entry(offset_minutes=offset, event=f"event_{offset}")
            )

        discarded = repository.prune(2)

        assert discarded == 3
        remaining = repository.list()
        assert [e.event for e in remaining] == ["event_3", "event_4"]

    def test_prune_is_a_no_op_when_under_limit(self):
        repository = InMemoryGovernanceLogRepository()

        repository.append(_entry())

        assert repository.prune(10) == 0
        assert len(repository.list()) == 1

    def test_prune_rejects_negative_max_entries(self):
        repository = InMemoryGovernanceLogRepository()

        with pytest.raises(ValueError):
            repository.prune(-1)

    def test_prune_older_than_discards_matching_entries(self):
        repository = InMemoryGovernanceLogRepository()

        old_entry = _entry(offset_minutes=0, event="old")
        new_entry = _entry(offset_minutes=100, event="new")

        repository.append(old_entry)
        repository.append(new_entry)

        cutoff = BASE_TIME + timedelta(minutes=50)

        discarded = repository.prune_older_than(cutoff)

        assert discarded == 1
        assert repository.list() == (new_entry,)

    def test_set_rotation_service_runs_after_append(self):
        repository = InMemoryGovernanceLogRepository()

        calls = []

        class _FakeRotationService:
            def rotate(self):
                calls.append(1)

        repository.set_rotation_service(_FakeRotationService())

        repository.append(_entry())

        assert calls == [1]


class TestSQLiteGovernanceLogRepository:

    def _database(self, tmp_path, name="logs.db"):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        return SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=tmp_path / name)
        )

    def test_list_is_empty_initially(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        assert repository.list() == ()
        assert repository.tail() == ()

    def test_append_then_list(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        entry = _entry(dispatch_id="dispatch-1")

        repository.append(entry)

        assert repository.list() == (entry,)

    def test_list_returns_oldest_first(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        first = _entry(offset_minutes=0, event="first")
        second = _entry(offset_minutes=1, event="second")
        third = _entry(offset_minutes=2, event="third")

        repository.append(first)
        repository.append(second)
        repository.append(third)

        assert repository.list() == (first, second, third)

    def test_tail_returns_newest_first(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        first = _entry(offset_minutes=0, event="first")
        second = _entry(offset_minutes=1, event="second")
        third = _entry(offset_minutes=2, event="third")

        repository.append(first)
        repository.append(second)
        repository.append(third)

        assert repository.tail() == (third, second, first)

    def test_tail_respects_limit(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        for offset in range(5):
            repository.append(
                _entry(offset_minutes=offset, event=f"event_{offset}")
            )

        limited = repository.tail(limit=2)

        assert len(limited) == 2
        assert limited[0].event == "event_4"
        assert limited[1].event == "event_3"

    def test_rejects_invalid_level(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        with pytest.raises(ValueError):
            repository.list(level="TRACE")

    def test_filters_by_level(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        info_entry = _entry(level="INFO")
        warning_entry = _entry(offset_minutes=1, level="WARNING")

        repository.append(info_entry)
        repository.append(warning_entry)

        assert repository.list(level="WARNING") == (warning_entry,)

    def test_filters_by_component(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        metrics_entry = _entry(component="metrics")
        engine_entry = _entry(
            offset_minutes=1, component="delivery_engine"
        )

        repository.append(metrics_entry)
        repository.append(engine_entry)

        assert repository.list(component="delivery_engine") == (
            engine_entry,
        )

    def test_fields_round_trip_through_json(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        entry = _entry(dispatch_id="dispatch-1", duration_ms=12.5)

        repository.append(entry)

        [stored] = repository.list()

        assert stored.fields == {
            "dispatch_id": "dispatch-1",
            "duration_ms": 12.5,
        }

    def test_clear_empties_repository(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        repository.append(_entry())
        repository.clear()

        assert repository.list() == ()

    def test_prune_discards_oldest_beyond_max_entries(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        for offset in range(5):
            repository.append(
                _entry(offset_minutes=offset, event=f"event_{offset}")
            )

        discarded = repository.prune(2)

        assert discarded == 3
        remaining = repository.list()
        assert [e.event for e in remaining] == ["event_3", "event_4"]

    def test_prune_is_a_no_op_when_under_limit(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        repository.append(_entry())

        assert repository.prune(10) == 0
        assert len(repository.list()) == 1

    def test_prune_older_than_discards_matching_entries(
        self, tmp_path
    ):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        repository.append(_entry(offset_minutes=0, event="old"))
        repository.append(_entry(offset_minutes=100, event="new"))

        cutoff = BASE_TIME + timedelta(minutes=50)

        discarded = repository.prune_older_than(cutoff)

        assert discarded == 1
        remaining = repository.list()
        assert [e.event for e in remaining] == ["new"]

    def test_set_rotation_service_runs_after_append(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )

        calls = []

        class _FakeRotationService:
            def rotate(self):
                calls.append(1)

        repository.set_rotation_service(_FakeRotationService())

        repository.append(_entry())

        assert calls == [1]

    def test_persists_and_survives_reload(self, tmp_path):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        database_path = tmp_path / "logs.db"

        database = SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=database_path)
        )

        repository = SQLiteGovernanceLogRepository(database)

        entry = _entry(dispatch_id="dispatch-1")

        repository.append(entry)

        reloaded_database = SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=database_path)
        )

        reloaded_repository = SQLiteGovernanceLogRepository(
            reloaded_database
        )

        assert reloaded_repository.list() == (entry,)
