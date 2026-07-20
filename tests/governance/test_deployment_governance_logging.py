import json
import threading
from datetime import datetime, timezone
from io import StringIO

import pytest
from unittest.mock import Mock

from backend.observability.deployment_governance_logging import (
    GovernanceLogEntry,
    GovernanceIntegrityLogger,
)
from backend.observability.deployment_governance_delivery_runtime import (
    GovernanceIntegrityDeliveryRuntime,
)
from backend.observability.deployment_governance_logging_cli import (
    _render_logging_failure,
    _render_logging_human,
    _render_logging_json,
    run_deployment_governance_logging_tail,
)

BASE_TIME = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


class TestGovernanceLogEntry:

    def test_valid_entry(self):
        entry = GovernanceLogEntry(
            timestamp=BASE_TIME,
            level="INFO",
            component="metrics",
            event="record_success",
            fields={"duration_ms": 12.5},
        )

        assert entry.timestamp == BASE_TIME
        assert entry.level == "INFO"
        assert entry.component == "metrics"
        assert entry.event == "record_success"
        assert entry.fields == {"duration_ms": 12.5}

    def test_rejects_naive_timestamp(self):
        with pytest.raises(ValueError):
            GovernanceLogEntry(
                timestamp=datetime(2026, 7, 20, 12, 0, 0),
                level="INFO",
                component="metrics",
                event="record_success",
                fields={},
            )

    def test_rejects_invalid_level(self):
        with pytest.raises(ValueError):
            GovernanceLogEntry(
                timestamp=BASE_TIME,
                level="TRACE",
                component="metrics",
                event="record_success",
                fields={},
            )

    def test_rejects_empty_component(self):
        with pytest.raises(ValueError):
            GovernanceLogEntry(
                timestamp=BASE_TIME,
                level="INFO",
                component="",
                event="record_success",
                fields={},
            )

    def test_rejects_empty_event(self):
        with pytest.raises(ValueError):
            GovernanceLogEntry(
                timestamp=BASE_TIME,
                level="INFO",
                component="metrics",
                event="",
                fields={},
            )

    def test_to_dict(self):
        entry = GovernanceLogEntry(
            timestamp=BASE_TIME,
            level="WARNING",
            component="delivery_engine",
            event="retry_scheduled",
            fields={"attempt": 1},
        )

        assert entry.to_dict() == {
            "timestamp": BASE_TIME.isoformat(),
            "level": "WARNING",
            "component": "delivery_engine",
            "event": "retry_scheduled",
            "fields": {"attempt": 1},
        }


class TestGovernanceIntegrityLoggerCreation:

    def test_rejects_non_positive_buffer_size(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityLogger(buffer_size=0)

    def test_debug_creates_entry(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        entry = logger.debug("metrics", "flush_started")

        assert entry.level == "DEBUG"
        assert entry.component == "metrics"
        assert entry.event == "flush_started"
        assert logger.entries() == (entry,)

    def test_info_creates_entry(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        entry = logger.info("delivery_engine", "delivery_succeeded")

        assert entry.level == "INFO"
        assert logger.entries() == (entry,)

    def test_warning_creates_entry(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        entry = logger.warning("delivery_engine", "retry_scheduled")

        assert entry.level == "WARNING"
        assert logger.entries() == (entry,)

    def test_error_creates_entry(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        entry = logger.error("delivery_engine", "delivery_failed")

        assert entry.level == "ERROR"
        assert logger.entries() == (entry,)


class TestGovernanceIntegrityLoggerStructuredFields:

    def test_fields_are_captured(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        entry = logger.info(
            "delivery_engine",
            "delivery_succeeded",
            dispatch_id="dispatch-1",
            duration_ms=42.0,
        )

        assert entry.fields == {
            "dispatch_id": "dispatch-1",
            "duration_ms": 42.0,
        }

    def test_fields_snapshot_is_independent_of_caller_dict(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        caller_fields = {"dispatch_id": "dispatch-1"}

        entry = logger.info(
            "delivery_engine", "delivery_succeeded", **caller_fields
        )

        caller_fields["dispatch_id"] = "mutated"

        assert entry.fields == {"dispatch_id": "dispatch-1"}


class TestGovernanceIntegrityLoggerExceptionLogging:

    def test_exception_outside_except_block_behaves_like_error(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        entry = logger.exception("delivery_engine", "unexpected_error")

        assert entry.level == "ERROR"
        assert "exception" not in entry.fields

    def test_exception_inside_except_block_captures_traceback(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        try:
            raise RuntimeError("boom")

        except RuntimeError:
            entry = logger.exception(
                "delivery_engine", "delivery_raised"
            )

        assert entry.level == "ERROR"
        assert "boom" in entry.fields["exception"]
        assert "RuntimeError" in entry.fields["exception"]


class TestGovernanceIntegrityLoggerLevelFiltering:

    def test_entries_are_newest_first(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        first = logger.info("metrics", "first")
        second = logger.info("metrics", "second")

        assert logger.entries() == (second, first)

    def test_entries_filtered_by_level(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        logger.info("metrics", "info_event")
        warning_entry = logger.warning("metrics", "warning_event")

        assert logger.entries(level="WARNING") == (warning_entry,)

    def test_entries_respects_limit(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        for index in range(5):
            logger.info("metrics", f"event_{index}")

        assert len(logger.entries(limit=2)) == 2

    def test_entries_rejects_invalid_level_filter(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        with pytest.raises(ValueError):
            logger.entries(level="TRACE")

    def test_buffer_is_bounded(self):
        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME, buffer_size=2
        )

        logger.info("metrics", "first")
        logger.info("metrics", "second")
        logger.info("metrics", "third")

        events = [entry.event for entry in logger.entries()]

        assert events == ["third", "second"]

    def test_clear_empties_buffer(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        logger.info("metrics", "first")
        logger.clear()

        assert logger.entries() == ()


class TestGovernanceIntegrityLoggerThreadSafety:

    def test_concurrent_logging_records_every_entry(self):
        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME, buffer_size=500
        )

        def _log_many():
            for _ in range(50):
                logger.info("metrics", "concurrent_event")

        threads = [
            threading.Thread(target=_log_many) for _ in range(4)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert len(logger.entries()) == 200


class TestGovernanceIntegrityLoggerRuntimeInjection:

    def test_runtime_stores_injected_logger(self):
        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            logger=logger,
        )

        assert runtime.logger is logger

    def test_runtime_defaults_to_no_logger(self):
        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
        )

        assert runtime.logger is None


class TestGovernanceIntegrityLoggingCli:

    def test_render_logging_human_empty(self):
        stdout = StringIO()

        _render_logging_human((), stdout=stdout)

        output = stdout.getvalue()

        assert "Governance Logs" in output
        assert "No governance log entries have been recorded." in output

    def test_render_logging_human_populated(self):
        entry = GovernanceLogEntry(
            timestamp=BASE_TIME,
            level="WARNING",
            component="delivery_engine",
            event="retry_scheduled",
            fields={"dispatch_id": "dispatch-1"},
        )

        stdout = StringIO()

        _render_logging_human((entry,), stdout=stdout)

        output = stdout.getvalue()

        assert "WARNING" in output
        assert "delivery_engine" in output
        assert "retry_scheduled" in output
        assert "dispatch_id=dispatch-1" in output

    def test_render_logging_json(self):
        entry = GovernanceLogEntry(
            timestamp=BASE_TIME,
            level="INFO",
            component="metrics",
            event="record_success",
            fields={"duration_ms": 1.0},
        )

        stdout = StringIO()

        _render_logging_json((entry,), stdout=stdout)

        payload = json.loads(stdout.getvalue())

        assert payload[0]["level"] == "INFO"
        assert payload[0]["component"] == "metrics"
        assert payload[0]["event"] == "record_success"

    def test_render_logging_failure_human(self):
        stderr = StringIO()

        _render_logging_failure(
            RuntimeError("simulated failure"),
            json_output=False,
            stderr=stderr,
        )

        assert "could not be produced" in stderr.getvalue()

    def test_render_logging_failure_json(self):
        stderr = StringIO()

        _render_logging_failure(
            RuntimeError("simulated failure"),
            json_output=True,
            stderr=stderr,
        )

        payload = json.loads(stderr.getvalue())

        assert payload["status"] == "execution_failed"
        assert payload["exit_code"] == 2

    def test_runner_handles_empty_history(self):
        stdout = StringIO()

        exit_code = run_deployment_governance_logging_tail(
            stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0
        assert "No governance log entries" in stdout.getvalue()

    def test_runner_rejects_invalid_level(self):
        stderr = StringIO()

        exit_code = run_deployment_governance_logging_tail(
            level="TRACE", stdout=StringIO(), stderr=stderr
        )

        assert exit_code == 2
        assert "could not be produced" in stderr.getvalue()

    def test_runner_filters_by_level_and_respects_limit(
        self, monkeypatch
    ):
        # The logger's buffer lives only in-process (unlike the
        # sqlite-backed repositories), so a real end-to-end test
        # would need two build_deployment_governance_persistence()
        # calls to see the same entries, which they never do (each
        # call constructs a fresh logger). Inject a pre-populated
        # stand-in runtime instead, mirroring what the runner
        # actually depends on: a build_integrity_logger() method.
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        logger.info("metrics", "info_event")
        logger.warning("metrics", "warning_event_1")
        logger.warning("metrics", "warning_event_2")

        class _StubRuntime:
            def build_integrity_logger(self) -> GovernanceIntegrityLogger:
                return logger

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: _StubRuntime(),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_tail(
            level="warning",
            limit=1,
            json_output=True,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert len(payload) == 1
        assert payload[0]["level"] == "WARNING"
