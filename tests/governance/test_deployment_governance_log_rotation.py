import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest
from unittest.mock import Mock

from backend.observability.deployment_governance_logging import (
    GovernanceLogEntry,
)
from backend.observability.deployment_governance_log_repository import (
    InMemoryGovernanceLogRepository,
)
from backend.observability.deployment_governance_log_rotation import (
    GovernanceLogRotationPolicy,
    GovernanceLogRotationService,
)
from backend.observability.deployment_governance_delivery_runtime import (
    GovernanceIntegrityDeliveryRuntime,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_rotate,
    run_deployment_governance_logging_rotation_status,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(*, offset_minutes: int = 0, event: str = "event") -> GovernanceLogEntry:
    return GovernanceLogEntry(
        timestamp=BASE_TIME + timedelta(minutes=offset_minutes),
        level="INFO",
        component="metrics",
        event=event,
        fields={},
    )


class TestGovernanceLogRotationPolicy:

    def test_rejects_negative_max_entries(self):
        with pytest.raises(ValueError):
            GovernanceLogRotationPolicy(max_entries=-1, max_age_days=None)

    def test_rejects_non_positive_max_age_days(self):
        with pytest.raises(ValueError):
            GovernanceLogRotationPolicy(max_entries=10, max_age_days=0)

    def test_allows_disabled_max_age(self):
        policy = GovernanceLogRotationPolicy(
            max_entries=10, max_age_days=None
        )

        assert policy.max_age_days is None

    def test_to_dict(self):
        policy = GovernanceLogRotationPolicy(
            max_entries=10, max_age_days=30
        )

        assert policy.to_dict() == {
            "max_entries": 10,
            "max_age_days": 30,
        }


class TestGovernanceLogRotationServiceCountPruning:

    def test_prune_keeps_only_newest_entries(self):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(_entry(offset_minutes=offset))

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=2, max_age_days=None
            ),
        )

        discarded = service.prune()

        assert discarded == 3
        remaining = repository.list()
        assert len(remaining) == 2
        assert [e.timestamp for e in remaining] == [
            BASE_TIME + timedelta(minutes=3),
            BASE_TIME + timedelta(minutes=4),
        ]

    def test_prune_is_a_no_op_when_under_limit(self):
        repository = InMemoryGovernanceLogRepository()

        repository.append(_entry())

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=10, max_age_days=None
            ),
        )

        assert service.prune() == 0
        assert len(repository.list()) == 1

    def test_prune_preserves_chronological_order(self):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(
                _entry(offset_minutes=offset, event=f"event_{offset}")
            )

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=3, max_age_days=None
            ),
        )

        service.prune()

        remaining = repository.list()

        assert [e.event for e in remaining] == [
            "event_2",
            "event_3",
            "event_4",
        ]


class TestGovernanceLogRotationServiceAgePruning:

    def test_rotate_discards_entries_older_than_max_age(self):
        repository = InMemoryGovernanceLogRepository()

        repository.append(_entry(offset_minutes=0, event="old"))
        repository.append(
            _entry(offset_minutes=60 * 24 * 10, event="new")
        )

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=1000, max_age_days=5
            ),
            clock=lambda: BASE_TIME + timedelta(days=10),
        )

        discarded = service.rotate()

        assert discarded == 1
        remaining = repository.list()
        assert len(remaining) == 1
        assert remaining[0].event == "new"

    def test_rotate_is_a_no_op_when_age_pruning_disabled(self):
        repository = InMemoryGovernanceLogRepository()

        repository.append(_entry(offset_minutes=0))

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=1000, max_age_days=None
            ),
            clock=lambda: BASE_TIME + timedelta(days=365),
        )

        assert service.rotate() == 0
        assert len(repository.list()) == 1


class TestGovernanceLogRotationServiceCombinedPruning:

    def test_rotate_applies_age_then_count(self):
        repository = InMemoryGovernanceLogRepository()

        # Two entries old enough to be age-pruned.
        repository.append(_entry(offset_minutes=0, event="ancient_1"))
        repository.append(_entry(offset_minutes=1, event="ancient_2"))

        # Five recent entries, more than max_entries allows.
        for offset in range(5):
            repository.append(
                _entry(
                    offset_minutes=60 * 24 * 20 + offset,
                    event=f"recent_{offset}",
                )
            )

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=3, max_age_days=10
            ),
            clock=lambda: BASE_TIME + timedelta(days=20),
        )

        discarded = service.rotate()

        # 2 discarded for age, then 2 more (5 recent - 3 max) for count.
        assert discarded == 4
        remaining = repository.list()
        assert [e.event for e in remaining] == [
            "recent_2",
            "recent_3",
            "recent_4",
        ]

    def test_rotate_is_idempotent(self):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(_entry(offset_minutes=offset))

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=2, max_age_days=None
            ),
        )

        first_pass = service.rotate()
        second_pass = service.rotate()

        assert first_pass == 3
        assert second_pass == 0
        assert len(repository.list()) == 2


class TestGovernanceLogRotationServiceEmptyRepository:

    def test_prune_on_empty_repository(self):
        repository = InMemoryGovernanceLogRepository()

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=10, max_age_days=None
            ),
        )

        assert service.prune() == 0

    def test_rotate_on_empty_repository(self):
        repository = InMemoryGovernanceLogRepository()

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=10, max_age_days=30
            ),
        )

        assert service.rotate() == 0


class TestGovernanceLogRotationServicePolicyAndReconfigure:

    def test_policy_returns_configured_defaults_when_none_given(self):
        repository = InMemoryGovernanceLogRepository()

        service = GovernanceLogRotationService(repository)

        policy = service.policy()

        assert policy.max_entries > 0
        assert policy.max_age_days is not None

    def test_reconfigure_replaces_only_given_fields(self):
        repository = InMemoryGovernanceLogRepository()

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=10, max_age_days=30
            ),
        )

        service.reconfigure(max_entries=5)

        policy = service.policy()

        assert policy.max_entries == 5
        assert policy.max_age_days == 30

    def test_reconfigure_can_disable_age_pruning(self):
        repository = InMemoryGovernanceLogRepository()

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=10, max_age_days=30
            ),
        )

        service.reconfigure(max_age_days=None)

        assert service.policy().max_age_days is None


class TestGovernanceLogRotationRepositoryWriteThrough:

    def test_append_triggers_rotation_when_attached(self):
        repository = InMemoryGovernanceLogRepository()

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=2, max_age_days=None
            ),
        )

        repository.set_rotation_service(service)

        for offset in range(5):
            repository.append(_entry(offset_minutes=offset))

        assert len(repository.list()) == 2

    def test_append_without_rotation_service_is_unbounded(self):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(_entry(offset_minutes=offset))

        assert len(repository.list()) == 5


class TestGovernanceLogRotationRuntimeIntegration:

    def test_start_runs_rotation(self):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(_entry(offset_minutes=offset))

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=2, max_age_days=None
            ),
        )

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            log_rotation_service=service,
        )

        runtime.start()

        assert len(repository.list()) == 2

    def test_runtime_defaults_to_no_rotation_service(self):
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

        assert runtime.log_rotation_service is None


class TestGovernanceLogRotationCli:

    def _stub_runtime(self, rotation_service):
        class _StubRuntime:
            def build_integrity_log_rotation_service(self):
                return rotation_service

        return _StubRuntime()

    def test_rotate_runner_reports_discarded_count(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(_entry(offset_minutes=offset))

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=2, max_age_days=None
            ),
        )

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_rotate(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["discarded"] == 3
        assert len(repository.list()) == 2

    def test_rotate_runner_respects_max_entries_override(
        self, monkeypatch
    ):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(_entry(offset_minutes=offset))

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=1000, max_age_days=None
            ),
        )

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_rotate(
            max_entries=1,
            json_output=True,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["discarded"] == 4
        assert len(repository.list()) == 1

    def test_rotation_status_runner_reports_policy(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=42, max_age_days=7
            ),
        )

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_rotation_status(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload == {"max_entries": 42, "max_age_days": 7}

    def test_rotation_status_runner_does_not_discard_anything(
        self, monkeypatch
    ):
        repository = InMemoryGovernanceLogRepository()

        for offset in range(5):
            repository.append(_entry(offset_minutes=offset))

        service = GovernanceLogRotationService(
            repository,
            policy=GovernanceLogRotationPolicy(
                max_entries=1, max_age_days=None
            ),
        )

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(service),
        )

        exit_code = run_deployment_governance_logging_rotation_status(
            stdout=StringIO(), stderr=StringIO()
        )

        assert exit_code == 0
        assert len(repository.list()) == 5

    def test_rotate_runner_handles_failure(self, monkeypatch):
        def _raise(config):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            _raise,
        )

        stderr = StringIO()

        exit_code = run_deployment_governance_logging_rotate(
            stdout=StringIO(), stderr=stderr
        )

        assert exit_code == 2
        assert "could not be completed" in stderr.getvalue()
