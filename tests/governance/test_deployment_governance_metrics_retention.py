from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_metrics import (
    GovernanceIntegrityMetrics,
)
from backend.observability.deployment_governance_metrics_history import (
    GovernanceIntegrityMetricsSnapshot,
    InMemoryGovernanceIntegrityMetricsHistoryRepository,
)
from backend.observability.deployment_governance_metrics_retention import (
    GovernanceIntegrityMetricsRetentionPolicy,
    GovernanceIntegrityMetricsRetentionService,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _metrics(total=1) -> GovernanceIntegrityMetrics:
    return GovernanceIntegrityMetrics(
        total_dispatches=total,
        successful_dispatches=total,
        failed_dispatches=0,
        retry_dispatches=0,
        average_duration_ms=0.0,
    )


def _repository_with_snapshots(
    count: int, *, interval: timedelta = timedelta(days=1)
) -> InMemoryGovernanceIntegrityMetricsHistoryRepository:
    repository = InMemoryGovernanceIntegrityMetricsHistoryRepository()

    for i in range(count):
        repository.append(
            GovernanceIntegrityMetricsSnapshot(
                captured_at=BASE_TIME + interval * i,
                metrics=_metrics(total=i + 1),
            )
        )

    return repository


class TestGovernanceIntegrityMetricsRetentionPolicy:

    def test_rejects_non_positive_max_age(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsRetentionPolicy(
                max_age=timedelta(0), max_entries=None
            )

    def test_rejects_negative_max_entries(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsRetentionPolicy(
                max_age=None, max_entries=-1
            )

    def test_allows_both_disabled(self):
        policy = GovernanceIntegrityMetricsRetentionPolicy(
            max_age=None, max_entries=None
        )

        assert policy.max_age is None
        assert policy.max_entries is None


class TestGovernanceIntegrityMetricsRetentionServiceRetentionPolicy:

    def test_retention_policy_reflects_configuration(self):
        repository = InMemoryGovernanceIntegrityMetricsHistoryRepository()

        service = GovernanceIntegrityMetricsRetentionService(
            repository,
            max_age=timedelta(days=7),
            max_entries=100,
        )

        policy = service.retention_policy()

        assert policy.max_age == timedelta(days=7)
        assert policy.max_entries == 100


class TestGovernanceIntegrityMetricsRetentionServiceEmptyHistory:

    def test_expired_is_empty_when_history_is_empty(self):
        repository = InMemoryGovernanceIntegrityMetricsHistoryRepository()

        service = GovernanceIntegrityMetricsRetentionService(
            repository, max_age=timedelta(days=1), max_entries=5
        )

        assert service.expired() == ()

    def test_prune_is_a_no_op_on_empty_history(self):
        repository = InMemoryGovernanceIntegrityMetricsHistoryRepository()

        service = GovernanceIntegrityMetricsRetentionService(
            repository, max_age=timedelta(days=1), max_entries=5
        )

        assert service.prune() == 0


class TestGovernanceIntegrityMetricsRetentionServiceCountBased:

    def test_prune_keeps_only_newest_max_entries(self):
        repository = _repository_with_snapshots(5)

        service = GovernanceIntegrityMetricsRetentionService(
            repository, max_age=None, max_entries=2
        )

        discarded = service.prune()

        assert discarded == 3

        remaining = repository.list()

        assert len(remaining) == 2
        assert remaining[0].metrics.total_dispatches == 5
        assert remaining[1].metrics.total_dispatches == 4

    def test_expired_returns_oldest_snapshots_in_chronological_order(
        self,
    ):
        repository = _repository_with_snapshots(5)

        service = GovernanceIntegrityMetricsRetentionService(
            repository, max_age=None, max_entries=2
        )

        expired = service.expired()

        assert len(expired) == 3
        # Oldest first: captured_at strictly increasing.
        assert (
            expired[0].captured_at
            < expired[1].captured_at
            < expired[2].captured_at
        )
        assert expired[0].metrics.total_dispatches == 1
        assert expired[-1].metrics.total_dispatches == 3

    def test_no_pruning_when_under_max_entries(self):
        repository = _repository_with_snapshots(3)

        service = GovernanceIntegrityMetricsRetentionService(
            repository, max_age=None, max_entries=10
        )

        assert service.prune() == 0
        assert len(repository.list()) == 3

    def test_disabled_max_entries_never_prunes_by_count(self):
        repository = _repository_with_snapshots(50)

        service = GovernanceIntegrityMetricsRetentionService(
            repository, max_age=None, max_entries=None
        )

        assert service.prune() == 0
        assert len(repository.list()) == 50


class TestGovernanceIntegrityMetricsRetentionServiceAgeBased:

    def test_prune_removes_snapshots_older_than_max_age(self):
        repository = _repository_with_snapshots(5)

        now = BASE_TIME + timedelta(days=4)

        service = GovernanceIntegrityMetricsRetentionService(
            repository,
            max_age=timedelta(days=2),
            max_entries=None,
            clock=lambda: now,
        )

        discarded = service.prune()

        # Snapshots at day 0 and day 1 are older than the cutoff
        # (day 4 - 2 days = day 2); days 2, 3, 4 survive.
        assert discarded == 2
        assert len(repository.list()) == 3

    def test_no_pruning_when_all_within_max_age(self):
        repository = _repository_with_snapshots(3)

        now = BASE_TIME + timedelta(days=2)

        service = GovernanceIntegrityMetricsRetentionService(
            repository,
            max_age=timedelta(days=30),
            max_entries=None,
            clock=lambda: now,
        )

        assert service.prune() == 0
        assert len(repository.list()) == 3

    def test_disabled_max_age_never_prunes_by_age(self):
        repository = _repository_with_snapshots(
            5, interval=timedelta(days=365)
        )

        now = BASE_TIME + timedelta(days=365 * 10)

        service = GovernanceIntegrityMetricsRetentionService(
            repository,
            max_age=None,
            max_entries=None,
            clock=lambda: now,
        )

        assert service.prune() == 0
        assert len(repository.list()) == 5


class TestGovernanceIntegrityMetricsRetentionServiceCombined:

    def test_combined_pruning_applies_stricter_of_both_rules(self):
        repository = _repository_with_snapshots(10)

        now = BASE_TIME + timedelta(days=9)

        # Age rule alone would keep the newest 4 (days 6-9).
        # Count rule alone would keep the newest 2.
        # Combined, the stricter (count) rule wins.
        service = GovernanceIntegrityMetricsRetentionService(
            repository,
            max_age=timedelta(days=3),
            max_entries=2,
            clock=lambda: now,
        )

        service.prune()

        remaining = repository.list()

        assert len(remaining) == 2
        assert remaining[0].metrics.total_dispatches == 10
        assert remaining[1].metrics.total_dispatches == 9

    def test_combined_pruning_age_stricter_than_count(self):
        repository = _repository_with_snapshots(10)

        now = BASE_TIME + timedelta(days=9)

        # Age rule keeps only the newest 2 (days 8-9).
        # Count rule alone would keep the newest 5.
        service = GovernanceIntegrityMetricsRetentionService(
            repository,
            max_age=timedelta(days=1),
            max_entries=5,
            clock=lambda: now,
        )

        service.prune()

        remaining = repository.list()

        assert len(remaining) == 2


class TestGovernanceIntegrityMetricsRetentionServiceOrdering:

    def test_repository_preserves_newest_first_after_prune(self):
        repository = _repository_with_snapshots(5)

        service = GovernanceIntegrityMetricsRetentionService(
            repository, max_age=None, max_entries=3
        )

        service.prune()

        remaining = repository.list()

        assert (
            remaining[0].captured_at
            > remaining[1].captured_at
            > remaining[2].captured_at
        )
