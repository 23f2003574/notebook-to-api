from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_bookmarks import (
    GovernanceIntegrityAuditBookmarkService,
    InMemoryGovernanceIntegrityAuditBookmarkRepository,
)
from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_labels import (
    GovernanceIntegrityAuditLabelService,
    InMemoryGovernanceIntegrityAuditLabelRepository,
)
from backend.observability.deployment_governance_audit_search import (
    GovernanceIntegrityAuditSearchQuery,
    GovernanceIntegrityAuditSearchService,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)


BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)


def make_record(
    *,
    audit_id: str,
    offset_minutes: int = 0,
    healthy: bool = True,
) -> GovernanceIntegrityAuditRecord:
    invalid_records = 0 if healthy else 1

    started_at = BASE_TIME + timedelta(minutes=offset_minutes)

    return GovernanceIntegrityAuditRecord(
        audit_id=audit_id,
        backend="sqlite",
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        outcome=(
            GovernanceIntegrityAuditOutcome.HEALTHY
            if healthy
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


class Harness:
    def __init__(self) -> None:
        self.history_repository = (
            InMemoryGovernanceIntegrityAuditHistoryRepository()
        )
        self.label_repository = (
            InMemoryGovernanceIntegrityAuditLabelRepository()
        )
        self.bookmark_repository = (
            InMemoryGovernanceIntegrityAuditBookmarkRepository()
        )

        self.label_service = GovernanceIntegrityAuditLabelService(
            self.label_repository, self.history_repository
        )
        self.bookmark_service = GovernanceIntegrityAuditBookmarkService(
            self.bookmark_repository, self.history_repository
        )
        self.search_service = GovernanceIntegrityAuditSearchService(
            self.history_repository,
            self.label_repository,
            self.bookmark_repository,
        )


# --- Query validation --------------------------------------------------


def test_query_rejects_no_filters() -> None:
    with pytest.raises(ValueError):
        GovernanceIntegrityAuditSearchQuery()


def test_query_accepts_single_filter() -> None:
    GovernanceIntegrityAuditSearchQuery(audit_id="A")
    GovernanceIntegrityAuditSearchQuery(healthy=True)
    GovernanceIntegrityAuditSearchQuery(label="release")
    GovernanceIntegrityAuditSearchQuery(bookmark="baseline")


# --- Search --------------------------------------------------------------


def test_search_by_audit_id() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="audit-1"))
    harness.history_repository.save(
        make_record(audit_id="audit-2", offset_minutes=10)
    )
    harness.history_repository.save(
        make_record(audit_id="audit-3", offset_minutes=20)
    )

    results = harness.search_service.search(
        GovernanceIntegrityAuditSearchQuery(audit_id="audit-3")
    )

    assert len(results) == 1
    assert results[0].audit_id == "audit-3"


def test_search_by_healthy_filter() -> None:
    harness = Harness()

    harness.history_repository.save(
        make_record(audit_id="A", offset_minutes=0, healthy=True)
    )
    harness.history_repository.save(
        make_record(audit_id="B", offset_minutes=10, healthy=False)
    )
    harness.history_repository.save(
        make_record(audit_id="C", offset_minutes=20, healthy=True)
    )

    results = harness.search_service.search(
        GovernanceIntegrityAuditSearchQuery(healthy=True)
    )

    assert [record.audit_id for record in results] == ["C", "A"]


def test_search_by_label_filter() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A", offset_minutes=0))
    harness.history_repository.save(make_record(audit_id="B", offset_minutes=10))
    harness.history_repository.save(make_record(audit_id="C", offset_minutes=20))

    harness.label_service.add("A", "release")
    harness.label_service.add("B", "baseline")
    harness.label_service.add("C", "release")

    results = harness.search_service.search(
        GovernanceIntegrityAuditSearchQuery(label="release")
    )

    assert {record.audit_id for record in results} == {"A", "C"}


def test_search_by_bookmark_filter() -> None:
    harness = Harness()

    for index in range(1, 6):
        harness.history_repository.save(
            make_record(audit_id=f"audit-{index}", offset_minutes=index)
        )

    harness.bookmark_service.create("stable", "audit-5")

    results = harness.search_service.search(
        GovernanceIntegrityAuditSearchQuery(bookmark="stable")
    )

    assert len(results) == 1
    assert results[0].audit_id == "audit-5"


def test_search_bookmark_filter_with_missing_bookmark_returns_empty() -> None:
    harness = Harness()

    harness.history_repository.save(make_record(audit_id="A"))

    results = harness.search_service.search(
        GovernanceIntegrityAuditSearchQuery(bookmark="missing")
    )

    assert results == ()


def test_search_combined_filters() -> None:
    harness = Harness()

    harness.history_repository.save(
        make_record(audit_id="A", offset_minutes=0, healthy=True)
    )
    harness.history_repository.save(
        make_record(audit_id="B", offset_minutes=10, healthy=False)
    )
    harness.history_repository.save(
        make_record(audit_id="C", offset_minutes=20, healthy=True)
    )

    harness.label_service.add("A", "release")
    harness.label_service.add("B", "release")
    harness.label_service.add("C", "baseline")

    results = harness.search_service.search(
        GovernanceIntegrityAuditSearchQuery(
            healthy=True, label="release"
        )
    )

    assert len(results) == 1
    assert results[0].audit_id == "A"


def test_search_preserves_repository_ordering() -> None:
    harness = Harness()

    for index in range(1, 5):
        harness.history_repository.save(
            make_record(audit_id=f"audit-{index}", offset_minutes=index)
        )

    results = harness.search_service.search(
        GovernanceIntegrityAuditSearchQuery(healthy=True)
    )

    assert [record.audit_id for record in results] == [
        "audit-4", "audit-3", "audit-2", "audit-1",
    ]


def test_runtime_builds_working_search_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "search-runtime.db"
        )
    )

    runtime.audit_history_repository.save(make_record(audit_id="A"))

    service = runtime.build_integrity_audit_search_service()

    results = service.search(
        GovernanceIntegrityAuditSearchQuery(audit_id="A")
    )

    assert len(results) == 1
