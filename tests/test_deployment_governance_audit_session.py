from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.observability.deployment_governance_audit_history import (
    GovernanceIntegrityAuditOutcome,
    GovernanceIntegrityAuditRecord,
    InMemoryGovernanceIntegrityAuditHistoryRepository,
)
from backend.observability.deployment_governance_audit_session import (
    GovernanceIntegrityAuditSession,
    GovernanceIntegrityAuditSessionService,
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


def test_session_model_rejects_mismatched_total() -> None:
    record = make_record(audit_id="A")

    with pytest.raises(
        ValueError, match="total_audits must match"
    ):
        GovernanceIntegrityAuditSession(
            records=(record,),
            total_audits=2,
            first_audit_id="A",
            latest_audit_id="A",
        )


def test_session_model_rejects_mismatched_latest_audit_id() -> None:
    record = make_record(audit_id="A")

    with pytest.raises(
        ValueError, match="latest_audit_id must match"
    ):
        GovernanceIntegrityAuditSession(
            records=(record,),
            total_audits=1,
            first_audit_id="A",
            latest_audit_id="B",
        )


def test_session_model_rejects_mismatched_first_audit_id() -> None:
    record = make_record(audit_id="A")

    with pytest.raises(
        ValueError, match="first_audit_id must match"
    ):
        GovernanceIntegrityAuditSession(
            records=(record,),
            total_audits=1,
            first_audit_id="B",
            latest_audit_id="A",
        )


def test_session_model_rejects_out_of_order_records() -> None:
    older = make_record(audit_id="A", offset_minutes=0)
    newer = make_record(audit_id="B", offset_minutes=10)

    with pytest.raises(
        ValueError, match="ordered newest to oldest"
    ):
        GovernanceIntegrityAuditSession(
            records=(older, newer),
            total_audits=2,
            first_audit_id="B",
            latest_audit_id="A",
        )


def test_session_handles_empty_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditSessionService(repository)

    session = service.session()

    assert session.total_audits == 0
    assert session.records == ()
    assert session.first_audit_id is None
    assert session.latest_audit_id is None


def test_session_returns_newest_first() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", offset_minutes=0))
    repository.save(make_record(audit_id="B", offset_minutes=10))
    repository.save(make_record(audit_id="C", offset_minutes=20))

    service = GovernanceIntegrityAuditSessionService(repository)

    session = service.session()

    assert [record.audit_id for record in session.records] == [
        "C", "B", "A",
    ]
    assert session.latest_audit_id == "C"
    assert session.first_audit_id == "A"


def test_latest_and_oldest() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", offset_minutes=0))
    repository.save(make_record(audit_id="B", offset_minutes=10))
    repository.save(make_record(audit_id="C", offset_minutes=20))

    service = GovernanceIntegrityAuditSessionService(repository)

    assert service.latest().audit_id == "C"
    assert service.oldest().audit_id == "A"


def test_latest_and_oldest_handle_empty_history() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditSessionService(repository)

    assert service.latest() is None
    assert service.oldest() is None


def test_audit_ids() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A", offset_minutes=0))
    repository.save(make_record(audit_id="B", offset_minutes=10))
    repository.save(make_record(audit_id="C", offset_minutes=20))

    service = GovernanceIntegrityAuditSessionService(repository)

    assert service.audit_ids() == ("C", "B", "A")


def test_session_respects_limit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    for index in range(5):
        repository.save(
            make_record(audit_id=f"audit-{index}", offset_minutes=index)
        )

    service = GovernanceIntegrityAuditSessionService(repository)

    session = service.session(limit=2)

    assert session.total_audits == 2


def test_session_rejects_non_positive_limit() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    service = GovernanceIntegrityAuditSessionService(repository)

    with pytest.raises(ValueError):
        service.session(limit=0)


def test_session_to_dict_reuses_record_serializer() -> None:
    repository = InMemoryGovernanceIntegrityAuditHistoryRepository()

    repository.save(make_record(audit_id="A"))

    service = GovernanceIntegrityAuditSessionService(repository)

    payload = service.session().to_dict()

    assert payload["total_audits"] == 1
    assert payload["latest_audit_id"] == "A"
    assert payload["first_audit_id"] == "A"
    assert payload["records"][0]["audit_id"] == "A"


def test_runtime_builds_working_session_service(tmp_path) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "session-runtime.db"
        )
    )

    runtime.audit_history_repository.save(
        make_record(audit_id="A")
    )

    service = runtime.build_integrity_audit_session_service()

    session = service.session()

    assert session.latest_audit_id == "A"
