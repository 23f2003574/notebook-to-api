from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_retention import (
    GovernanceIntegrityAuditAutomaticRetentionConfig,
    GovernanceIntegrityAuditRetentionPolicy,
    GovernanceIntegrityAuditRetentionService,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)


NOW = datetime(
    2026,
    7,
    15,
    12,
    0,
    tzinfo=timezone.utc,
)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    invalid_records: int = 0,
) -> GovernanceIntegrityAuditRecord:
    started_at = NOW + timedelta(minutes=offset_minutes)

    return make_record_at(
        audit_id=audit_id,
        started_at=started_at,
        invalid_records=invalid_records,
    )


def make_record_at(
    *,
    audit_id: str,
    started_at: datetime,
    invalid_records: int = 0,
) -> GovernanceIntegrityAuditRecord:
    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if invalid_records == 0
            else GovernanceIntegrityAuditOutcome.UNHEALTHY
        ),
        total_records=10,
        valid_records=10 - invalid_records,
        invalid_records=invalid_records,
        integrity_mismatches=invalid_records,
        missing_integrity_metadata=0,
        invalid_integrity_metadata=0,
        invalid_persisted_records=0,
    )


def test_policy_requires_at_least_one_limit() -> None:
    with pytest.raises(
        ValueError, match="at least one retention limit"
    ):
        GovernanceIntegrityAuditRetentionPolicy()


def test_policy_rejects_non_positive_max_records() -> None:
    with pytest.raises(
        ValueError, match="max_records must be greater than zero"
    ):
        GovernanceIntegrityAuditRetentionPolicy(max_records=0)


def test_policy_rejects_non_positive_max_age_days() -> None:
    with pytest.raises(
        ValueError, match="max_age_days must be greater than zero"
    ):
        GovernanceIntegrityAuditRetentionPolicy(max_age_days=0)


def test_retention_plan_prunes_records_beyond_max_count() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    for index in range(5):
        repository.save(
            make_record(audit_id=f"audit-{index}", offset_minutes=index)
        )

    service = GovernanceIntegrityAuditRetentionService(
        repository, clock=lambda: NOW
    )

    plan = service.plan(
        GovernanceIntegrityAuditRetentionPolicy(max_records=2)
    )

    assert plan.total_records == 5
    assert plan.retained_records == 2
    assert plan.prunable_records == 3

    assert plan.retained_audit_ids == ("audit-4", "audit-3")


def test_retention_plan_prunes_records_older_than_max_age() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record_at(
            audit_id="old",
            started_at=NOW - timedelta(days=40),
        )
    )

    repository.save(
        make_record_at(
            audit_id="recent",
            started_at=NOW - timedelta(days=5),
        )
    )

    service = GovernanceIntegrityAuditRetentionService(
        repository, clock=lambda: NOW
    )

    plan = service.plan(
        GovernanceIntegrityAuditRetentionPolicy(max_age_days=30)
    )

    assert plan.prunable_audit_ids == ("old",)


def test_retention_combines_count_and_age_limits() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record_at(audit_id="a", started_at=NOW - timedelta(days=1))
    )

    repository.save(
        make_record_at(audit_id="b", started_at=NOW - timedelta(days=5))
    )

    repository.save(
        make_record_at(audit_id="c", started_at=NOW - timedelta(days=20))
    )

    repository.save(
        make_record_at(audit_id="d", started_at=NOW - timedelta(days=50))
    )

    service = GovernanceIntegrityAuditRetentionService(
        repository, clock=lambda: NOW
    )

    # max_records=3, max_age_days=30: only d violates either limit.
    plan = service.plan(
        GovernanceIntegrityAuditRetentionPolicy(
            max_records=3, max_age_days=30
        )
    )

    assert set(plan.retained_audit_ids) == {"a", "b", "c"}
    assert plan.prunable_audit_ids == ("d",)

    # max_records=2, max_age_days=30: c is pruned by count, d by both.
    plan = service.plan(
        GovernanceIntegrityAuditRetentionPolicy(
            max_records=2, max_age_days=30
        )
    )

    assert set(plan.retained_audit_ids) == {"a", "b"}
    assert set(plan.prunable_audit_ids) == {"c", "d"}
    assert len(plan.prunable_audit_ids) == 2  # each ID appears once


def test_retention_keeps_record_exactly_on_age_boundary() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record_at(
            audit_id="boundary",
            started_at=NOW - timedelta(days=30),
        )
    )

    repository.save(
        make_record_at(
            audit_id="newer",
            started_at=NOW - timedelta(days=1),
        )
    )

    service = GovernanceIntegrityAuditRetentionService(
        repository, clock=lambda: NOW
    )

    plan = service.plan(
        GovernanceIntegrityAuditRetentionPolicy(max_age_days=30)
    )

    assert "boundary" not in plan.prunable_audit_ids


def test_retention_preserves_latest_record_by_default() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record_at(
            audit_id="only-audit",
            started_at=NOW - timedelta(days=365),
        )
    )

    service = GovernanceIntegrityAuditRetentionService(
        repository, clock=lambda: NOW
    )

    plan = service.plan(
        GovernanceIntegrityAuditRetentionPolicy(max_age_days=30)
    )

    assert plan.prunable_records == 0
    assert plan.retained_audit_ids == ("only-audit",)


def test_retention_can_prune_latest_when_preservation_is_disabled() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(
        make_record_at(
            audit_id="only-audit",
            started_at=NOW - timedelta(days=365),
        )
    )

    service = GovernanceIntegrityAuditRetentionService(
        repository, clock=lambda: NOW
    )

    plan = service.plan(
        GovernanceIntegrityAuditRetentionPolicy(
            max_age_days=30, preserve_latest=False
        )
    )

    assert plan.prunable_audit_ids == ("only-audit",)


def test_retention_prune_is_dry_run_by_default() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    for index in range(5):
        repository.save(
            make_record(audit_id=f"audit-{index}", offset_minutes=index)
        )

    service = GovernanceIntegrityAuditRetentionService(
        repository, clock=lambda: NOW
    )

    result = service.prune(
        GovernanceIntegrityAuditRetentionPolicy(max_records=2)
    )

    assert result.applied is False
    assert result.deleted_records == 0
    assert repository.count() == 5


def test_retention_prune_deletes_planned_records_when_applied() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    for index in range(5):
        repository.save(
            make_record(audit_id=f"audit-{index}", offset_minutes=index)
        )

    service = GovernanceIntegrityAuditRetentionService(
        repository, clock=lambda: NOW
    )

    result = service.prune(
        GovernanceIntegrityAuditRetentionPolicy(max_records=2),
        apply=True,
    )

    assert result.applied is True
    assert result.deleted_records == 3
    assert repository.count() == 2


def test_retention_plan_handles_empty_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditRetentionService(
        repository, clock=lambda: NOW
    )

    plan = service.plan(
        GovernanceIntegrityAuditRetentionPolicy(max_records=10)
    )

    assert plan.total_records == 0
    assert plan.retained_records == 0
    assert plan.prunable_records == 0
    assert plan.has_prunable_records is False
    assert plan.oldest_retained_started_at is None
    assert plan.newest_retained_started_at is None


def test_sqlite_audit_history_pruning_persists_after_runtime_recreation(
    tmp_path,
) -> None:
    database_path = tmp_path / "audit-retention.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    repository = runtime.audit_history_repository

    for index in range(5):
        repository.save(
            make_record(audit_id=f"audit-{index}", offset_minutes=index)
        )

    result = runtime.build_integrity_audit_retention_service().prune(
        GovernanceIntegrityAuditRetentionPolicy(max_records=2),
        apply=True,
    )

    assert result.deleted_records == 3

    recreated_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    assert recreated_runtime.audit_history_repository.count() == 2


def test_sqlite_automatic_retention_persists_bounded_history(
    tmp_path,
) -> None:
    database_path = tmp_path / "automatic-retention.db"

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path),
        automatic_audit_retention=(
            GovernanceIntegrityAuditAutomaticRetentionConfig(
                enabled=True, max_records=2
            )
        ),
    )

    recording_service = (
        runtime.build_integrity_audit_recording_service()
    )

    for _ in range(5):
        recording_service.audit_and_record()

    assert runtime.audit_history_repository.count() == 2

    recreated_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(database_path)
    )

    assert recreated_runtime.audit_history_repository.count() == 2
