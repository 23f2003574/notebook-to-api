from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_replay import (
    GovernanceIntegrityAuditReplay,
    GovernanceIntegrityAuditReplayService,
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


def test_replay_rejects_mismatched_audit_id() -> None:
    record = make_record(audit_id="audit-a")

    with pytest.raises(
        ValueError, match="audit_id must match record.audit_id"
    ):
        GovernanceIntegrityAuditReplay(
            audit_id="audit-b",
            record=record,
            replayed_at=BASE_TIME,
        )


def test_replay_rejects_naive_replayed_at() -> None:
    record = make_record(audit_id="audit-a")

    with pytest.raises(
        ValueError, match="replayed_at must be timezone-aware"
    ):
        GovernanceIntegrityAuditReplay(
            audit_id="audit-a",
            record=record,
            replayed_at=datetime(2026, 7, 15, 23, 0, 0),
        )


def test_replay_to_dict_reuses_record_serializer() -> None:
    record = make_record(audit_id="audit-a")

    replay = GovernanceIntegrityAuditReplay(
        audit_id="audit-a",
        record=record,
        replayed_at=BASE_TIME,
    )

    payload = replay.to_dict()

    assert payload["audit_id"] == "audit-a"
    assert payload["replayed_at"] == BASE_TIME.isoformat()
    assert payload["record"]["audit_id"] == "audit-a"
    assert payload["record"]["total_records"] == 10


def test_replay_latest_raises_for_empty_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditReplayService(repository)

    with pytest.raises(LookupError):
        service.replay_latest()


def test_replay_raises_for_missing_audit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditReplayService(repository)

    with pytest.raises(KeyError):
        service.replay("missing")


def test_replay_latest_returns_newest_audit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", offset_minutes=0))
    repository.save(make_record(audit_id="B", offset_minutes=10))
    repository.save(make_record(audit_id="C", offset_minutes=20))

    service = GovernanceIntegrityAuditReplayService(repository)

    replay = service.replay_latest()

    assert replay.audit_id == "C"


def test_replay_by_id_returns_correct_record() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", offset_minutes=0))
    repository.save(make_record(audit_id="B", offset_minutes=10))
    repository.save(make_record(audit_id="C", offset_minutes=20))

    service = GovernanceIntegrityAuditReplayService(repository)

    replay = service.replay("B")

    assert replay.audit_id == "B"
    assert replay.record.audit_id == "B"


def test_replay_recent_returns_newest_first() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", offset_minutes=0))
    repository.save(make_record(audit_id="B", offset_minutes=10))
    repository.save(make_record(audit_id="C", offset_minutes=20))
    repository.save(make_record(audit_id="D", offset_minutes=30))

    service = GovernanceIntegrityAuditReplayService(repository)

    replays = service.replay_recent(limit=2)

    assert [replay.audit_id for replay in replays] == ["D", "C"]


def test_replay_recent_rejects_non_positive_limit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditReplayService(repository)

    with pytest.raises(ValueError):
        service.replay_recent(limit=0)


def test_replay_uses_default_clock() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A"))

    service = GovernanceIntegrityAuditReplayService(repository)

    replay = service.replay_latest()

    assert replay.replayed_at.tzinfo is not None


def test_replay_uses_injected_clock() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A"))

    fixed_time = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)

    service = GovernanceIntegrityAuditReplayService(
        repository, clock=lambda: fixed_time
    )

    replay = service.replay_latest()

    assert replay.replayed_at == fixed_time


def test_runtime_builds_working_replay_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "replay-runtime.db"
        )
    )

    runtime.audit_history_repository.save(
        make_record(audit_id="A")
    )

    service = runtime.build_integrity_audit_replay_service()

    replay = service.replay_latest()

    assert replay.audit_id == "A"
