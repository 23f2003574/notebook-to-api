import csv
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
from backend.observability.deployment_governance_log_export import (
    GovernanceLogExportService,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_export_csv,
    run_deployment_governance_logging_export_json,
    run_deployment_governance_logging_export_ndjson,
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


def _seed(repository) -> tuple:
    entries = (
        _entry(
            offset_minutes=0,
            level="INFO",
            component="metrics",
            event="record_success",
            duration_ms=5.0,
        ),
        _entry(
            offset_minutes=1,
            level="WARNING",
            component="delivery_engine",
            event="retry_scheduled",
            dispatch_id="d1",
        ),
        _entry(
            offset_minutes=2,
            level="ERROR",
            component="delivery_engine",
            event="delivery_failed",
            dispatch_id="d1",
        ),
    )

    for entry in entries:
        repository.append(entry)

    return entries


def _export_service(repository) -> GovernanceLogExportService:
    return GovernanceLogExportService(
        GovernanceLogSearchService(repository)
    )


class TestGovernanceLogExportServiceJson:

    def test_export_json_writes_array_newest_first(self):
        repository = InMemoryGovernanceLogRepository()
        a, b, c = _seed(repository)

        service = _export_service(repository)

        stream = StringIO()

        count = service.export_json(stream)

        assert count == 3

        payload = json.loads(stream.getvalue())

        assert [item["event"] for item in payload] == [
            "delivery_failed",
            "retry_scheduled",
            "record_success",
        ]

    def test_export_json_uses_utc_iso8601_timestamps(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = _export_service(repository)

        stream = StringIO()

        service.export_json(stream)

        payload = json.loads(stream.getvalue())

        for item in payload:
            assert item["timestamp"].endswith("+00:00")
            # Round-trips through fromisoformat without error.
            datetime.fromisoformat(item["timestamp"])

    def test_export_json_deterministic_field_order(self):
        repository = InMemoryGovernanceLogRepository()
        repository.append(_entry())

        service = _export_service(repository)

        stream = StringIO()

        service.export_json(stream)

        raw = stream.getvalue()

        # to_dict()'s declared order is preserved (not alphabetized).
        first_object = raw.split("\n")[1]

        assert first_object.index('"timestamp"') < first_object.index(
            '"level"'
        )
        assert first_object.index('"level"') < first_object.index(
            '"component"'
        )
        assert first_object.index('"component"') < first_object.index(
            '"event"'
        )

    def test_export_json_empty_repository_writes_empty_array(self):
        repository = InMemoryGovernanceLogRepository()

        service = _export_service(repository)

        stream = StringIO()

        count = service.export_json(stream)

        assert count == 0
        assert json.loads(stream.getvalue()) == []


class TestGovernanceLogExportServiceCsv:

    def test_export_csv_has_fixed_header(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = _export_service(repository)

        stream = StringIO()

        service.export_csv(stream)

        reader = csv.reader(StringIO(stream.getvalue()))

        header = next(reader)

        assert header == [
            "timestamp",
            "level",
            "component",
            "event",
            "fields_json",
        ]

    def test_export_csv_preserves_newest_first_order(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = _export_service(repository)

        stream = StringIO()

        count = service.export_csv(stream)

        assert count == 3

        reader = csv.DictReader(StringIO(stream.getvalue()))

        rows = list(reader)

        assert [row["event"] for row in rows] == [
            "delivery_failed",
            "retry_scheduled",
            "record_success",
        ]

    def test_export_csv_json_encodes_fields_column(self):
        repository = InMemoryGovernanceLogRepository()
        repository.append(_entry(dispatch_id="d1", duration_ms=12.5))

        service = _export_service(repository)

        stream = StringIO()

        service.export_csv(stream)

        reader = csv.DictReader(StringIO(stream.getvalue()))

        row = next(reader)

        assert json.loads(row["fields_json"]) == {
            "dispatch_id": "d1",
            "duration_ms": 12.5,
        }

    def test_export_csv_empty_repository_writes_only_header(self):
        repository = InMemoryGovernanceLogRepository()

        service = _export_service(repository)

        stream = StringIO()

        count = service.export_csv(stream)

        assert count == 0

        reader = csv.reader(StringIO(stream.getvalue()))

        rows = list(reader)

        assert len(rows) == 1


class TestGovernanceLogExportServiceNdjson:

    def test_export_ndjson_one_object_per_line(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = _export_service(repository)

        stream = StringIO()

        count = service.export_ndjson(stream)

        assert count == 3

        lines = stream.getvalue().strip("\n").split("\n")

        assert len(lines) == 3

        for line in lines:
            json.loads(line)

    def test_export_ndjson_preserves_newest_first_order(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = _export_service(repository)

        stream = StringIO()

        service.export_ndjson(stream)

        lines = stream.getvalue().strip("\n").split("\n")

        events = [json.loads(line)["event"] for line in lines]

        assert events == [
            "delivery_failed",
            "retry_scheduled",
            "record_success",
        ]

    def test_export_ndjson_empty_repository_writes_nothing(self):
        repository = InMemoryGovernanceLogRepository()

        service = _export_service(repository)

        stream = StringIO()

        count = service.export_ndjson(stream)

        assert count == 0
        assert stream.getvalue() == ""


class TestGovernanceLogExportServiceFilters:

    def test_export_json_filters_by_level(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = _export_service(repository)

        stream = StringIO()

        count = service.export_json(stream, level="ERROR")

        assert count == 1
        payload = json.loads(stream.getvalue())
        assert payload[0]["event"] == "delivery_failed"

    def test_export_csv_filters_by_component(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = _export_service(repository)

        stream = StringIO()

        count = service.export_csv(
            stream, component="delivery_engine"
        )

        assert count == 2

        reader = csv.DictReader(StringIO(stream.getvalue()))

        assert {row["component"] for row in reader} == {
            "delivery_engine"
        }

    def test_export_ndjson_filters_by_time_range(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = _export_service(repository)

        stream = StringIO()

        count = service.export_ndjson(
            stream,
            since=BASE_TIME + timedelta(minutes=1),
            until=BASE_TIME + timedelta(minutes=2),
        )

        assert count == 2

    def test_export_json_combines_filters(self):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        service = _export_service(repository)

        stream = StringIO()

        count = service.export_json(
            stream, level="WARNING", component="delivery_engine"
        )

        assert count == 1


class TestGovernanceLogExportServiceStreaming:

    def test_export_streams_across_multiple_search_batches(self):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(
                _entry(offset_minutes=offset, event=f"event_{offset}")
            )

        search_service = GovernanceLogSearchService(repository)

        service = GovernanceLogExportService(search_service)

        stream = StringIO()

        # Force multiple internal iter_search batches.
        count = service.export_ndjson(stream)

        assert count == 5

        lines = stream.getvalue().strip("\n").split("\n")

        events = [json.loads(line)["event"] for line in lines]

        assert events == [
            "event_4",
            "event_3",
            "event_2",
            "event_1",
            "event_0",
        ]

    def test_iter_search_respects_small_batch_size(self):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(
                _entry(offset_minutes=offset, event=f"event_{offset}")
            )

        search_service = GovernanceLogSearchService(repository)

        results = list(
            search_service.iter_search(batch_size=2)
        )

        assert [entry.event for entry in results] == [
            "event_4",
            "event_3",
            "event_2",
            "event_1",
            "event_0",
        ]

    def test_iter_search_rejects_non_positive_batch_size(self):
        repository = InMemoryGovernanceLogRepository()

        search_service = GovernanceLogSearchService(repository)

        with pytest.raises(ValueError):
            list(search_service.iter_search(batch_size=0))


class TestGovernanceLogExportCli:

    def _stub_runtime(self, repository):
        class _StubRuntime:
            def build_integrity_log_export_service(self):
                return _export_service(repository)

        return _StubRuntime()

    def test_export_json_runner_writes_file(
        self, monkeypatch, tmp_path
    ):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        output_path = tmp_path / "logs.json"

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_export_json(
            output_path=output_path,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0
        assert "Exported 3" in stdout.getvalue()

        payload = json.loads(output_path.read_text())

        assert len(payload) == 3

    def test_export_csv_runner_writes_file(
        self, monkeypatch, tmp_path
    ):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        output_path = tmp_path / "logs.csv"

        exit_code = run_deployment_governance_logging_export_csv(
            output_path=output_path,
            component="delivery_engine",
            stdout=StringIO(),
            stderr=StringIO(),
        )

        assert exit_code == 0

        reader = csv.DictReader(output_path.open())

        rows = list(reader)

        assert len(rows) == 2

    def test_export_ndjson_runner_writes_file(
        self, monkeypatch, tmp_path
    ):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        output_path = tmp_path / "logs.ndjson"

        exit_code = run_deployment_governance_logging_export_ndjson(
            output_path=output_path,
            stdout=StringIO(),
            stderr=StringIO(),
        )

        assert exit_code == 0

        lines = output_path.read_text().strip("\n").split("\n")

        assert len(lines) == 3

    def test_export_runner_creates_parent_directories(
        self, monkeypatch, tmp_path
    ):
        repository = InMemoryGovernanceLogRepository()
        _seed(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        output_path = tmp_path / "nested" / "dir" / "logs.json"

        exit_code = run_deployment_governance_logging_export_json(
            output_path=output_path,
            stdout=StringIO(),
            stderr=StringIO(),
        )

        assert exit_code == 0
        assert output_path.exists()

    def test_export_runner_handles_empty_repository(
        self, monkeypatch, tmp_path
    ):
        repository = InMemoryGovernanceLogRepository()

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(repository),
        )

        output_path = tmp_path / "empty.json"

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_export_json(
            output_path=output_path,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0
        assert "Exported 0" in stdout.getvalue()
        assert json.loads(output_path.read_text()) == []

    def test_export_runner_rejects_invalid_level(self, tmp_path):
        stderr = StringIO()

        exit_code = run_deployment_governance_logging_export_json(
            output_path=tmp_path / "logs.json",
            level="TRACE",
            stdout=StringIO(),
            stderr=stderr,
        )

        assert exit_code == 2
        assert "could not be completed" in stderr.getvalue()
