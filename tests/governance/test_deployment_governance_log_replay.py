import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest

from backend.observability.deployment_governance_logging import (
    GovernanceLogEntry,
)
from backend.observability.deployment_governance_log_repository import (
    InMemoryGovernanceLogRepository,
)
from backend.observability.deployment_governance_log_search import (
    GovernanceLogSearchService,
)
from backend.observability.deployment_governance_log_replay import (
    GovernanceLogReplayCursor,
    GovernanceLogReplayService,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_replay,
    run_deployment_governance_logging_replay_next,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(
    *,
    offset_minutes: int = 0,
    event: str = "event",
) -> GovernanceLogEntry:
    return GovernanceLogEntry(
        timestamp=BASE_TIME + timedelta(minutes=offset_minutes),
        level="INFO",
        component="metrics",
        event=event,
        fields={},
    )


def _seed(repository) -> list:
    entries = [
        _entry(offset_minutes=i, event=f"event_{i}")
        for i in range(5)
    ]

    for entry in entries:
        repository.append(entry)

    return entries


def _replay_service(repository, **kwargs) -> GovernanceLogReplayService:
    return GovernanceLogReplayService(
        GovernanceLogSearchService(repository), **kwargs
    )


class TestGovernanceLogReplayCursor:

    def test_rejects_negative_position(self):
        with pytest.raises(ValueError):
            GovernanceLogReplayCursor(position=-1, timestamp=None)

    def test_to_dict_with_timestamp(self):
        cursor = GovernanceLogReplayCursor(
            position=2, timestamp=BASE_TIME
        )

        assert cursor.to_dict() == {
            "position": 2,
            "timestamp": BASE_TIME.isoformat(),
        }

    def test_to_dict_without_timestamp(self):
        cursor = GovernanceLogReplayCursor(position=5, timestamp=None)

        assert cursor.to_dict() == {
            "position": 5,
            "timestamp": None,
        }


class TestGovernanceLogReplayServiceFullReplay:

    def test_replay_returns_chronological_order(self):
        repository = InMemoryGovernanceLogRepository()

        entries = _seed(repository)

        service = _replay_service(repository)

        assert service.replay() == tuple(entries)

    def test_replay_respects_limit(self):
        repository = InMemoryGovernanceLogRepository()

        entries = _seed(repository)

        service = _replay_service(repository)

        assert service.replay(limit=2) == tuple(entries[:2])

    def test_replay_does_not_move_cursor(self):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        service = _replay_service(repository)

        service.replay()

        assert service.cursor().position == 0


class TestGovernanceLogReplayServiceTimestampSeek:

    def test_seek_to_timestamp_lands_on_first_matching_entry(self):
        repository = InMemoryGovernanceLogRepository()

        entries = _seed(repository)

        service = _replay_service(repository)

        cursor = service.seek(
            timestamp=BASE_TIME + timedelta(minutes=2)
        )

        assert cursor.position == 2
        assert cursor.timestamp == entries[2].timestamp

    def test_seek_to_timestamp_past_the_end(self):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        service = _replay_service(repository)

        cursor = service.seek(
            timestamp=BASE_TIME + timedelta(minutes=100)
        )

        assert cursor.position == 5
        assert cursor.timestamp is None

    def test_seek_to_position(self):
        repository = InMemoryGovernanceLogRepository()

        entries = _seed(repository)

        service = _replay_service(repository)

        cursor = service.seek(position=3)

        assert cursor.position == 3
        assert cursor.timestamp == entries[3].timestamp

    def test_seek_to_position_beyond_end_clamps(self):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        service = _replay_service(repository)

        cursor = service.seek(position=999)

        assert cursor.position == 5
        assert cursor.timestamp is None

    def test_seek_requires_exactly_one_argument(self):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        service = _replay_service(repository)

        with pytest.raises(ValueError):
            service.seek()

        with pytest.raises(ValueError):
            service.seek(timestamp=BASE_TIME, position=0)

    def test_seek_rejects_negative_position(self):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        service = _replay_service(repository)

        with pytest.raises(ValueError):
            service.seek(position=-1)

    def test_next_after_seek_starts_from_seeked_position(self):
        repository = InMemoryGovernanceLogRepository()

        entries = _seed(repository)

        service = _replay_service(repository)

        service.seek(position=3)

        batch = service.next(limit=2)

        assert batch == tuple(entries[3:5])


class TestGovernanceLogReplayServiceEventFiltering:

    def test_event_filter_scopes_the_whole_stream(self):
        repository = InMemoryGovernanceLogRepository()

        repository.append(_entry(offset_minutes=0, event="wanted"))
        repository.append(_entry(offset_minutes=1, event="other"))
        repository.append(_entry(offset_minutes=2, event="wanted"))

        service = _replay_service(repository, event="wanted")

        replayed = service.replay()

        assert len(replayed) == 2
        assert all(entry.event == "wanted" for entry in replayed)

    def test_since_filter_scopes_the_whole_stream(self):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        service = _replay_service(
            repository, since=BASE_TIME + timedelta(minutes=3)
        )

        replayed = service.replay()

        assert [e.event for e in replayed] == ["event_3", "event_4"]

    def test_since_and_event_combine(self):
        repository = InMemoryGovernanceLogRepository()

        repository.append(_entry(offset_minutes=0, event="wanted"))
        repository.append(_entry(offset_minutes=1, event="wanted"))
        repository.append(_entry(offset_minutes=2, event="other"))

        service = _replay_service(
            repository,
            since=BASE_TIME + timedelta(minutes=1),
            event="wanted",
        )

        replayed = service.replay()

        assert len(replayed) == 1
        assert replayed[0].event == "wanted"


class TestGovernanceLogReplayServiceCursorProgression:

    def test_next_advances_cursor_by_default_one(self):
        repository = InMemoryGovernanceLogRepository()

        entries = _seed(repository)

        service = _replay_service(repository)

        first = service.next()

        assert first == (entries[0],)
        assert service.cursor().position == 1

        second = service.next()

        assert second == (entries[1],)
        assert service.cursor().position == 2

    def test_next_respects_limit(self):
        repository = InMemoryGovernanceLogRepository()

        entries = _seed(repository)

        service = _replay_service(repository)

        batch = service.next(limit=3)

        assert batch == tuple(entries[:3])
        assert service.cursor().position == 3

    def test_next_returns_fewer_entries_once_exhausted(self):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        service = _replay_service(repository)

        service.next(limit=4)

        remaining = service.next(limit=10)

        assert len(remaining) == 1

        exhausted = service.next(limit=1)

        assert exhausted == ()

    def test_next_rejects_non_positive_limit(self):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        service = _replay_service(repository)

        with pytest.raises(ValueError):
            service.next(limit=0)

    def test_reset_returns_cursor_to_start(self):
        repository = InMemoryGovernanceLogRepository()

        entries = _seed(repository)

        service = _replay_service(repository)

        service.next(limit=3)

        cursor = service.reset()

        assert cursor.position == 0
        assert cursor.timestamp == entries[0].timestamp

        assert service.next() == (entries[0],)

    def test_cursor_timestamp_none_at_end_of_stream(self):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        service = _replay_service(repository)

        service.next(limit=5)

        cursor = service.cursor()

        assert cursor.position == 5
        assert cursor.timestamp is None


class TestGovernanceLogReplayServiceEmptyRepository:

    def test_replay_on_empty_repository(self):
        repository = InMemoryGovernanceLogRepository()

        service = _replay_service(repository)

        assert service.replay() == ()

    def test_next_on_empty_repository(self):
        repository = InMemoryGovernanceLogRepository()

        service = _replay_service(repository)

        assert service.next() == ()

    def test_cursor_on_empty_repository(self):
        repository = InMemoryGovernanceLogRepository()

        service = _replay_service(repository)

        cursor = service.cursor()

        assert cursor.position == 0
        assert cursor.timestamp is None

    def test_seek_on_empty_repository(self):
        repository = InMemoryGovernanceLogRepository()

        service = _replay_service(repository)

        cursor = service.seek(position=0)

        assert cursor.position == 0
        assert cursor.timestamp is None


class TestGovernanceLogReplayServiceNoMutation:

    def test_replay_and_next_never_modify_repository(self):
        repository = InMemoryGovernanceLogRepository()

        entries = _seed(repository)

        service = _replay_service(repository)

        service.replay()
        service.next(limit=3)
        service.seek(position=1)
        service.reset()

        assert list(repository.list()) == entries


class TestGovernanceLogReplaySnapshotIsolation:

    def test_replay_stream_is_a_point_in_time_snapshot(self):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        service = _replay_service(repository)

        # Trigger snapshot capture.
        service.replay()

        # Appended after the snapshot was taken.
        repository.append(_entry(offset_minutes=100, event="late"))

        assert "late" not in [e.event for e in service.replay()]


class TestGovernanceLogReplayCli:

    def _stub_runtime(self, repository):
        class _StubRuntime:
            def build_integrity_log_replay_service(
                self, *, since=None, event=None
            ):
                return GovernanceLogReplayService(
                    GovernanceLogSearchService(repository),
                    since=since,
                    event=event,
                )

        return _StubRuntime()

    def test_replay_runner_returns_chronological_order(
        self, monkeypatch
    ):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_replay(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert [item["event"] for item in payload] == [
            f"event_{i}" for i in range(5)
        ]

    def test_replay_runner_respects_limit(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        stdout = StringIO()

        run_deployment_governance_logging_replay(
            limit=2, json_output=True, stdout=stdout, stderr=StringIO()
        )

        payload = json.loads(stdout.getvalue())

        assert len(payload) == 2

    def test_replay_runner_filters_by_event(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()

        repository.append(_entry(offset_minutes=0, event="wanted"))
        repository.append(_entry(offset_minutes=1, event="other"))

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        stdout = StringIO()

        run_deployment_governance_logging_replay(
            event="wanted",
            json_output=True,
            stdout=stdout,
            stderr=StringIO(),
        )

        payload = json.loads(stdout.getvalue())

        assert len(payload) == 1
        assert payload[0]["event"] == "wanted"

    def test_replay_next_runner_advances_and_reports_cursor(
        self, monkeypatch
    ):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_replay_next(
            limit=2, json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert len(payload["entries"]) == 2
        assert payload["entries"][0]["event"] == "event_0"
        assert payload["cursor"]["position"] == 2

    def test_replay_next_runner_defaults_to_one(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()

        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        stdout = StringIO()

        run_deployment_governance_logging_replay_next(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        payload = json.loads(stdout.getvalue())

        assert len(payload["entries"]) == 1
        assert payload["cursor"]["position"] == 1

    def test_replay_runner_handles_empty_repository(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_replay(
            stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0
        assert "No governance log entries" in stdout.getvalue()

    def test_replay_runner_handles_failure(self, monkeypatch):
        def _raise(config):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            _raise,
        )

        stderr = StringIO()

        exit_code = run_deployment_governance_logging_replay(
            stdout=StringIO(), stderr=stderr
        )

        assert exit_code == 2
        assert "could not be completed" in stderr.getvalue()
