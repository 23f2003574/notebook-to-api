import json
from datetime import datetime, timezone
from io import StringIO

import pytest

from backend.observability.deployment_governance_logging import (
    GovernanceIntegrityLogger,
    GovernanceLogEntry,
)
from backend.observability.deployment_governance_log_repository import (
    InMemoryGovernanceLogRepository,
)
from backend.observability.deployment_governance_log_sampling import (
    GovernanceLogSamplingPolicy,
    GovernanceLogSamplingService,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_sampling_show,
    run_deployment_governance_logging_sampling_update,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(
    *,
    level: str = "INFO",
    event: str = "record_success",
) -> GovernanceLogEntry:
    return GovernanceLogEntry(
        timestamp=BASE_TIME,
        level=level,
        component="metrics",
        event=event,
        fields={},
    )


class TestGovernanceLogSamplingPolicy:

    def test_default_policy_logs_everything(self):
        policy = GovernanceLogSamplingPolicy()

        assert policy.default_rate == 1.0
        assert policy.per_level == {}
        assert policy.always_log_events == frozenset()

    def test_rejects_default_rate_above_one(self):
        with pytest.raises(ValueError):
            GovernanceLogSamplingPolicy(default_rate=1.1)

    def test_rejects_default_rate_below_zero(self):
        with pytest.raises(ValueError):
            GovernanceLogSamplingPolicy(default_rate=-0.1)

    def test_rejects_invalid_per_level_rate(self):
        with pytest.raises(ValueError):
            GovernanceLogSamplingPolicy(per_level={"INFO": 2.0})

    def test_accepts_boundary_rates(self):
        policy = GovernanceLogSamplingPolicy(
            default_rate=0.0, per_level={"INFO": 1.0}
        )

        assert policy.default_rate == 0.0
        assert policy.per_level["INFO"] == 1.0

    def test_rate_for_level_falls_back_to_default(self):
        policy = GovernanceLogSamplingPolicy(
            default_rate=0.5, per_level={"ERROR": 1.0}
        )

        assert policy.rate_for_level("INFO") == 0.5
        assert policy.rate_for_level("ERROR") == 1.0

    def test_to_dict(self):
        policy = GovernanceLogSamplingPolicy(
            default_rate=0.5,
            per_level={"WARNING": 0.2},
            always_log_events=frozenset({"important_event"}),
        )

        assert policy.to_dict() == {
            "default_rate": 0.5,
            "per_level": {"WARNING": 0.2},
            "always_log_events": ["important_event"],
        }


class TestGovernanceLogSamplingServiceDefaultBehavior:

    def test_default_policy_always_logs(self):
        service = GovernanceLogSamplingService()

        for _ in range(20):
            assert service.should_log(_entry()) is True

    def test_rate_zero_never_logs_non_error(self):
        service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.0)
        )

        for index in range(50):
            assert (
                service.should_log(
                    _entry(event=f"routine_event_{index}")
                )
                is False
            )


class TestGovernanceLogSamplingServicePerLevelOverride:

    def test_per_level_rate_overrides_default(self):
        service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(
                default_rate=0.0, per_level={"WARNING": 1.0}
            )
        )

        assert service.should_log(_entry(level="WARNING")) is True
        assert service.should_log(_entry(level="INFO")) is False

    def test_per_level_rate_zero_can_suppress_a_level(self):
        service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(
                default_rate=1.0, per_level={"DEBUG": 0.0}
            )
        )

        assert service.should_log(_entry(level="DEBUG")) is False
        assert service.should_log(_entry(level="INFO")) is True


class TestGovernanceLogSamplingServiceErrorBypass:

    def test_error_always_logged_even_at_rate_zero(self):
        service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(
                default_rate=0.0, per_level={"ERROR": 0.0}
            )
        )

        assert service.should_log(_entry(level="ERROR")) is True

    def test_critical_always_logged_even_at_rate_zero(self):
        service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.0)
        )

        assert (
            service.should_log(_entry(level="ERROR", event="x"))
            is True
        )

    def test_always_log_events_bypasses_rate(self):
        service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(
                default_rate=0.0,
                always_log_events=frozenset({"critical_event"}),
            )
        )

        assert (
            service.should_log(_entry(event="critical_event"))
            is True
        )
        assert (
            service.should_log(_entry(event="routine_event"))
            is False
        )


class TestGovernanceLogSamplingServiceDeterminism:

    def test_same_event_always_gets_same_decision(self):
        service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.5)
        )

        decisions = {
            service.should_log(_entry(event="stable_event"))
            for _ in range(50)
        }

        assert len(decisions) == 1

    def test_decision_is_stable_across_service_instances(self):
        policy = GovernanceLogSamplingPolicy(default_rate=0.5)

        first_service = GovernanceLogSamplingService(policy)
        second_service = GovernanceLogSamplingService(policy)

        entry = _entry(event="cross_instance_event")

        assert first_service.should_log(
            entry
        ) == second_service.should_log(entry)

    def test_different_events_can_get_different_decisions(self):
        service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.5)
        )

        decisions = {
            service.should_log(_entry(event=f"event_{i}"))
            for i in range(200)
        }

        # At rate 0.5 over 200 distinct events, both outcomes should
        # appear -- this is not a statistical fluke check on ratio,
        # just confirming the hash actually varies by event name
        # rather than always returning the same decision.
        assert decisions == {True, False}

    def test_approximate_rate_over_many_distinct_events(self):
        service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.3)
        )

        kept = sum(
            1
            for i in range(2000)
            if service.should_log(_entry(event=f"event_{i}"))
        )

        # Deterministic-per-event hashing should still land close to
        # the configured rate in aggregate over many distinct events.
        assert 500 <= kept <= 700


class TestGovernanceLogSamplingServicePolicyUpdate:

    def test_policy_returns_configured_policy(self):
        policy = GovernanceLogSamplingPolicy(default_rate=0.7)

        service = GovernanceLogSamplingService(policy)

        assert service.policy() == policy

    def test_update_policy_replaces_it(self):
        service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=1.0)
        )

        new_policy = GovernanceLogSamplingPolicy(default_rate=0.0)

        service.update_policy(new_policy)

        assert service.policy() == new_policy
        assert service.should_log(_entry()) is False


class TestGovernanceLogSamplingLoggerIntegration:

    def test_dropped_entry_is_not_persisted(self):
        repository = InMemoryGovernanceLogRepository()

        sampling_service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.0)
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            sampling_service=sampling_service,
        )

        logger.info("metrics", "routine_event")

        assert repository.list() == ()

    def test_kept_entry_is_persisted(self):
        repository = InMemoryGovernanceLogRepository()

        sampling_service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=1.0)
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            sampling_service=sampling_service,
        )

        logger.info("metrics", "routine_event")

        assert len(repository.list()) == 1

    def test_dropped_entry_still_appears_in_in_memory_buffer(self):
        repository = InMemoryGovernanceLogRepository()

        sampling_service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.0)
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            sampling_service=sampling_service,
        )

        logger.info("metrics", "routine_event")

        assert len(logger.entries()) == 1
        assert repository.list() == ()

    def test_dropped_entry_still_returned_to_caller(self):
        sampling_service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.0)
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            sampling_service=sampling_service,
        )

        entry = logger.info("metrics", "routine_event")

        assert entry.event == "routine_event"

    def test_error_bypasses_sampling_through_logger(self):
        repository = InMemoryGovernanceLogRepository()

        sampling_service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.0)
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            sampling_service=sampling_service,
        )

        logger.error("metrics", "something_broke")

        assert len(repository.list()) == 1

    def test_broken_sampling_service_fails_open(self):
        repository = InMemoryGovernanceLogRepository()

        class _BrokenSamplingService:
            def should_log(self, entry):
                raise RuntimeError("boom")

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            sampling_service=_BrokenSamplingService(),
        )

        entry = logger.info("metrics", "record_success")

        assert entry.event == "record_success"
        assert len(repository.list()) == 1

    def test_no_sampling_service_persists_everything(self):
        repository = InMemoryGovernanceLogRepository()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME, repository=repository
        )

        for i in range(10):
            logger.info("metrics", f"event_{i}")

        assert len(repository.list()) == 10

    def test_set_sampling_service_attaches_after_construction(self):
        repository = InMemoryGovernanceLogRepository()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME, repository=repository
        )

        logger.set_sampling_service(
            GovernanceLogSamplingService(
                GovernanceLogSamplingPolicy(default_rate=0.0)
            )
        )

        logger.info("metrics", "routine_event")

        assert repository.list() == ()


class TestGovernanceLogSamplingCli:

    def _stub_runtime(self, sampling_service):
        class _StubRuntime:
            def build_integrity_log_sampling_service(self):
                return sampling_service

        return _StubRuntime()

    def test_show_runner_reports_policy(self, monkeypatch):
        sampling_service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(
                default_rate=0.4, per_level={"DEBUG": 0.1}
            )
        )

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(sampling_service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_sampling_show(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["default_rate"] == 0.4
        assert payload["per_level"] == {"DEBUG": 0.1}

    def test_update_runner_sets_default_rate(self, monkeypatch):
        sampling_service = GovernanceLogSamplingService()

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(sampling_service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_sampling_update(
            default_rate=0.2,
            json_output=True,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["default_rate"] == 0.2
        assert sampling_service.policy().default_rate == 0.2

    def test_update_runner_sets_per_level_override(self, monkeypatch):
        sampling_service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.9)
        )

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(sampling_service),
        )

        exit_code = run_deployment_governance_logging_sampling_update(
            level="warning",
            rate=0.05,
            stdout=StringIO(),
            stderr=StringIO(),
        )

        assert exit_code == 0

        policy = sampling_service.policy()

        assert policy.per_level["WARNING"] == 0.05
        # Unrelated existing value preserved.
        assert policy.default_rate == 0.9

    def test_update_runner_rejects_level_without_rate(
        self, monkeypatch
    ):
        sampling_service = GovernanceLogSamplingService()

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(sampling_service),
        )

        stderr = StringIO()

        exit_code = run_deployment_governance_logging_sampling_update(
            level="ERROR", stdout=StringIO(), stderr=stderr
        )

        assert exit_code == 2
        assert "could not be completed" in stderr.getvalue()

    def test_update_runner_rejects_invalid_level(self, monkeypatch):
        sampling_service = GovernanceLogSamplingService()

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(sampling_service),
        )

        stderr = StringIO()

        exit_code = run_deployment_governance_logging_sampling_update(
            level="TRACE",
            rate=0.5,
            stdout=StringIO(),
            stderr=stderr,
        )

        assert exit_code == 2

    def test_update_runner_rejects_invalid_rate(self, monkeypatch):
        sampling_service = GovernanceLogSamplingService()

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(sampling_service),
        )

        stderr = StringIO()

        exit_code = run_deployment_governance_logging_sampling_update(
            default_rate=2.0, stdout=StringIO(), stderr=stderr
        )

        assert exit_code == 2
