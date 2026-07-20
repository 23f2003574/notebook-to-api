import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest

from backend.observability.deployment_governance_logging import (
    GovernanceLogEntry,
)
from backend.observability.deployment_governance_log_repository import (
    InMemoryGovernanceLogRepository,
    SQLiteGovernanceLogRepository,
)
from backend.observability.deployment_governance_log_search import (
    GovernanceLogSearchService,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_search,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(
    *,
    offset_minutes: int = 0,
    level: str = "INFO",
    component: str = "metrics",
    event: str = "record_success",
) -> GovernanceLogEntry:
    return GovernanceLogEntry(
        timestamp=BASE_TIME + timedelta(minutes=offset_minutes),
        level=level,
        component=component,
        event=event,
        fields={},
    )


def _seed(repository) -> dict:
    entries = {
        "a": _entry(
            offset_minutes=0,
            level="INFO",
            component="metrics",
            event="record_success",
        ),
        "b": _entry(
            offset_minutes=1,
            level="WARNING",
            component="delivery_engine",
            event="retry_scheduled",
        ),
        "c": _entry(
            offset_minutes=2,
            level="ERROR",
            component="delivery_engine",
            event="delivery_failed",
        ),
        "d": _entry(
            offset_minutes=3,
            level="INFO",
            component="delivery_runtime",
            event="runtime_started",
        ),
    }

    for entry in entries.values():
        repository.append(entry)

    return entries


class TestGovernanceLogSearchServiceByLevel:

    def test_search_by_level(self):
        repository = InMemoryGovernanceLogRepository()
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        assert service.by_level("WARNING") == (entries["b"],)

    def test_search_filters_by_level(self):
        repository = InMemoryGovernanceLogRepository()
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        assert service.search(level="ERROR") == (entries["c"],)


class TestGovernanceLogSearchServiceByComponent:

    def test_search_by_component(self):
        repository = InMemoryGovernanceLogRepository()
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        # newest first: c (offset 2) then b (offset 1)
        assert service.by_component("delivery_engine") == (
            entries["c"],
            entries["b"],
        )


class TestGovernanceLogSearchServiceByEvent:

    def test_search_by_event(self):
        repository = InMemoryGovernanceLogRepository()
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        assert service.by_event("runtime_started") == (entries["d"],)


class TestGovernanceLogSearchServiceTimeRange:

    def test_between_is_inclusive_on_both_ends(self):
        repository = InMemoryGovernanceLogRepository()
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        since = BASE_TIME + timedelta(minutes=1)
        until = BASE_TIME + timedelta(minutes=2)

        assert service.between(since, until) == (
            entries["c"],
            entries["b"],
        )

    def test_between_excludes_entries_outside_range(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = GovernanceLogSearchService(repository)

        since = BASE_TIME + timedelta(minutes=10)
        until = BASE_TIME + timedelta(minutes=20)

        assert service.between(since, until) == ()


class TestGovernanceLogSearchServiceCombinedFilters:

    def test_search_combines_level_and_component(self):
        repository = InMemoryGovernanceLogRepository()
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        assert service.search(
            level="INFO", component="delivery_runtime"
        ) == (entries["d"],)

    def test_search_combines_component_and_time_range(self):
        repository = InMemoryGovernanceLogRepository()
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        assert service.search(
            component="delivery_engine",
            until=BASE_TIME + timedelta(minutes=1),
        ) == (entries["b"],)

    def test_search_with_no_matches_returns_empty(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = GovernanceLogSearchService(repository)

        assert (
            service.search(level="ERROR", component="metrics") == ()
        )


class TestGovernanceLogSearchServicePagination:

    def test_search_respects_limit(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = GovernanceLogSearchService(repository)

        assert len(service.search(limit=2)) == 2

    def test_search_respects_offset(self):
        repository = InMemoryGovernanceLogRepository()
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        # newest first: d, c, b, a -- skip the newest one
        result = service.search(offset=1)

        assert result == (
            entries["c"],
            entries["b"],
            entries["a"],
        )

    def test_search_combines_limit_and_offset(self):
        repository = InMemoryGovernanceLogRepository()
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        result = service.search(offset=1, limit=2)

        assert result == (entries["c"], entries["b"])

    def test_count_ignores_pagination(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = GovernanceLogSearchService(repository)

        assert service.count() == 4
        assert service.count(component="delivery_engine") == 2

    def test_search_rejects_negative_offset(self):
        repository = InMemoryGovernanceLogRepository()

        service = GovernanceLogSearchService(repository)

        with pytest.raises(ValueError):
            service.search(offset=-1)


class TestGovernanceLogSearchRepositorySQLite:

    def _database(self, tmp_path, name="search.db"):
        from backend.persistence.sqlite_database import (
            SQLiteDatabase,
            SQLiteDatabaseConfig,
        )

        return SQLiteDatabase(
            SQLiteDatabaseConfig(database_path=tmp_path / name)
        )

    def test_search_by_level_and_component(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        assert service.search(
            level="ERROR", component="delivery_engine"
        ) == (entries["c"],)

    def test_search_pagination(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        result = service.search(offset=1, limit=2)

        assert result == (entries["c"], entries["b"])

    def test_search_time_range_inclusive(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        since = BASE_TIME + timedelta(minutes=1)
        until = BASE_TIME + timedelta(minutes=2)

        assert service.between(since, until) == (
            entries["c"],
            entries["b"],
        )

    def test_count_matches_search_length_without_pagination(
        self, tmp_path
    ):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )
        _seed(repository)

        service = GovernanceLogSearchService(repository)

        assert service.count() == len(repository.search())

    def test_offset_without_limit_still_works(self, tmp_path):
        repository = SQLiteGovernanceLogRepository(
            self._database(tmp_path)
        )
        entries = _seed(repository)

        service = GovernanceLogSearchService(repository)

        result = service.search(offset=2)

        assert result == (entries["b"], entries["a"])


class TestGovernanceLogSearchCli:

    def _stub_runtime(self, repository):
        class _StubRuntime:
            def build_integrity_log_search_service(self):
                return GovernanceLogSearchService(repository)

        return _StubRuntime()

    def test_search_runner_filters_by_component(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()
        entries = _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_search(
            component="delivery_engine",
            json_output=True,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert [entry["event"] for entry in payload] == [
            "delivery_failed",
            "retry_scheduled",
        ]

    def test_search_runner_supports_time_range(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_search(
            since=BASE_TIME + timedelta(minutes=1),
            until=BASE_TIME + timedelta(minutes=2),
            json_output=True,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert len(payload) == 2

    def test_search_runner_supports_pagination(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_search(
            limit=2,
            offset=1,
            json_output=True,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert len(payload) == 2

    def test_search_runner_rejects_invalid_level(self):
        stderr = StringIO()

        exit_code = run_deployment_governance_logging_search(
            level="TRACE", stdout=StringIO(), stderr=stderr
        )

        assert exit_code == 2
        assert "could not be completed" in stderr.getvalue()

    def test_search_runner_handles_no_matches(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_search(
            component="nonexistent",
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0
        assert "No governance log entries" in stdout.getvalue()
