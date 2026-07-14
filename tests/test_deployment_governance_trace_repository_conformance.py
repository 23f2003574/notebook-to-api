from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.observability.deployment_governance_trace_repository import (
    DeploymentGovernanceTraceRepository,
    GovernanceTraceQuery,
    GovernanceTraceRecord,
)

from backend.observability.in_memory_deployment_governance_trace_repository import (
    GovernanceTraceAlreadyExistsError,
    GovernanceTraceNotFoundError,
    InMemoryDeploymentGovernanceTraceRepository,
)

from backend.observability.sqlite_deployment_governance_trace_repository import (
    SQLiteDeploymentGovernanceTraceRepository,
)

from backend.persistence.sqlite_database import (
    SQLiteDatabase,
    SQLiteDatabaseConfig,
)


RepositoryFactory = Callable[
    [],
    DeploymentGovernanceTraceRepository,
]


BASE_TIME = datetime(
    2026,
    7,
    14,
    8,
    0,
    0,
    tzinfo=timezone.utc,
)


def make_record(
    *,
    trace_id: str,
    deployment_id: str,
    service_name: str = "payments-api",
    environment: str = "production",
    artifact_digest: str | None = None,
    created_at: datetime = BASE_TIME,
    updated_at: datetime | None = None,
    governance_state: str = "created",
    final_status: str | None = None,
    completed: bool = False,
) -> GovernanceTraceRecord:
    """
    Build deterministic repository records for conformance testing.
    """

    if artifact_digest is None:
        artifact_digest = (
            f"sha256:{trace_id}"
        )

    if updated_at is None:
        updated_at = created_at

    return GovernanceTraceRecord(
        trace_id=trace_id,
        deployment_id=deployment_id,
        service_name=service_name,
        environment=environment,
        artifact_digest=artifact_digest,
        created_at=created_at,
        updated_at=updated_at,
        governance_state=governance_state,
        final_status=final_status,
        completed=completed,
        payload={
            "schema_version": 1,
            "trace": {
                "trace_id": trace_id,
                "deployment_id": deployment_id,
                "service_name": service_name,
                "environment": environment,
                "artifact_digest": artifact_digest,
                "created_at": created_at.isoformat(),
            },
            "events": [],
        },
    )


@pytest.fixture(
    params=(
        "in_memory",
        "sqlite",
    )
)
def repository(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> DeploymentGovernanceTraceRepository:
    """
    Run every repository conformance test against every implementation.
    """

    if request.param == "in_memory":
        return (
            InMemoryDeploymentGovernanceTraceRepository()
        )

    if request.param == "sqlite":
        database = SQLiteDatabase(
            SQLiteDatabaseConfig(
                database_path=(
                    tmp_path
                    / "governance-conformance.db"
                ),
            )
        )

        return (
            SQLiteDeploymentGovernanceTraceRepository(
                database
            )
        )

    raise AssertionError(
        "unsupported repository test parameter "
        f"'{request.param}'"
    )


def test_repository_starts_empty(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    assert repository.count() == 0

    assert repository.list() == ()

    assert (
        repository.get_by_trace_id(
            "missing-trace"
        )
        is None
    )

    assert (
        repository.get_by_deployment_id(
            "missing-deployment"
        )
        is None
    )


def test_save_and_retrieve_by_trace_id(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    record = make_record(
        trace_id="trace-001",
        deployment_id="deployment-001",
    )

    saved = repository.save(
        record
    )

    restored = repository.get_by_trace_id(
        "trace-001"
    )

    assert saved == record
    assert restored == record


def test_save_and_retrieve_by_deployment_id(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    record = make_record(
        trace_id="trace-002",
        deployment_id="deployment-002",
    )

    repository.save(
        record
    )

    restored = (
        repository.get_by_deployment_id(
            "deployment-002"
        )
    )

    assert restored == record


def test_exists_reflects_persisted_state(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    record = make_record(
        trace_id="trace-003",
        deployment_id="deployment-003",
    )

    assert (
        repository.exists(
            record.trace_id
        )
        is False
    )

    repository.save(
        record
    )

    assert (
        repository.exists(
            record.trace_id
        )
        is True
    )


def test_duplicate_trace_id_is_rejected(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    first = make_record(
        trace_id="trace-duplicate",
        deployment_id="deployment-first",
    )

    second = make_record(
        trace_id="trace-duplicate",
        deployment_id="deployment-second",
    )

    repository.save(
        first
    )

    with pytest.raises(
        GovernanceTraceAlreadyExistsError
    ):
        repository.save(
            second
        )

    assert repository.count() == 1

    assert (
        repository.get_by_trace_id(
            "trace-duplicate"
        )
        == first
    )


def test_duplicate_deployment_id_is_rejected(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    first = make_record(
        trace_id="trace-first",
        deployment_id="deployment-duplicate",
    )

    second = make_record(
        trace_id="trace-second",
        deployment_id="deployment-duplicate",
    )

    repository.save(
        first
    )

    with pytest.raises(
        GovernanceTraceAlreadyExistsError
    ):
        repository.save(
            second
        )

    assert repository.count() == 1


def test_update_replaces_existing_record(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    original = make_record(
        trace_id="trace-update",
        deployment_id="deployment-update",
        governance_state="created",
    )

    updated = make_record(
        trace_id="trace-update",
        deployment_id="deployment-update",
        governance_state="awaiting_approval",
        updated_at=(
            BASE_TIME
            + timedelta(
                minutes=5
            )
        ),
    )

    repository.save(
        original
    )

    result = repository.update(
        updated
    )

    assert result == updated

    assert (
        repository.get_by_trace_id(
            "trace-update"
        )
        == updated
    )


def test_update_missing_record_is_rejected(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    record = make_record(
        trace_id="trace-missing-update",
        deployment_id="deployment-missing-update",
    )

    with pytest.raises(
        GovernanceTraceNotFoundError
    ):
        repository.update(
            record
        )


def test_update_cannot_claim_another_deployment_id(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    first = make_record(
        trace_id="trace-update-first",
        deployment_id="deployment-update-first",
    )

    second = make_record(
        trace_id="trace-update-second",
        deployment_id="deployment-update-second",
    )

    repository.save(
        first
    )

    repository.save(
        second
    )

    conflicting_update = make_record(
        trace_id="trace-update-second",
        deployment_id="deployment-update-first",
    )

    with pytest.raises(
        GovernanceTraceAlreadyExistsError
    ):
        repository.update(
            conflicting_update
        )

    assert (
        repository.get_by_trace_id(
            "trace-update-second"
        )
        == second
    )


def test_upsert_inserts_missing_record(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    record = make_record(
        trace_id="trace-upsert-new",
        deployment_id="deployment-upsert-new",
    )

    result = repository.upsert(
        record
    )

    assert result == record

    assert (
        repository.get_by_trace_id(
            record.trace_id
        )
        == record
    )


def test_upsert_updates_existing_trace(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    original = make_record(
        trace_id="trace-upsert-existing",
        deployment_id="deployment-upsert-existing",
        governance_state="created",
    )

    updated = make_record(
        trace_id="trace-upsert-existing",
        deployment_id="deployment-upsert-existing",
        governance_state="succeeded",
        final_status="succeeded",
        completed=True,
        updated_at=(
            BASE_TIME
            + timedelta(
                minutes=10
            )
        ),
    )

    repository.save(
        original
    )

    repository.upsert(
        updated
    )

    assert (
        repository.get_by_trace_id(
            original.trace_id
        )
        == updated
    )


def test_upsert_does_not_overwrite_different_trace_for_same_deployment(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    existing = make_record(
        trace_id="trace-upsert-owner",
        deployment_id="deployment-upsert-shared",
    )

    conflicting = make_record(
        trace_id="trace-upsert-conflict",
        deployment_id="deployment-upsert-shared",
    )

    repository.save(
        existing
    )

    with pytest.raises(
        GovernanceTraceAlreadyExistsError
    ):
        repository.upsert(
            conflicting
        )

    assert (
        repository.get_by_deployment_id(
            "deployment-upsert-shared"
        )
        == existing
    )


def test_save_many_persists_all_records(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    records = (
        make_record(
            trace_id="trace-batch-001",
            deployment_id="deployment-batch-001",
        ),
        make_record(
            trace_id="trace-batch-002",
            deployment_id="deployment-batch-002",
        ),
        make_record(
            trace_id="trace-batch-003",
            deployment_id="deployment-batch-003",
        ),
    )

    result = repository.save_many(
        records
    )

    assert tuple(result) == records
    assert repository.count() == 3


def test_save_many_is_atomic_on_duplicate_trace_id(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    records = (
        make_record(
            trace_id="trace-atomic-001",
            deployment_id="deployment-atomic-001",
        ),
        make_record(
            trace_id="trace-atomic-001",
            deployment_id="deployment-atomic-002",
        ),
    )

    with pytest.raises(
        GovernanceTraceAlreadyExistsError
    ):
        repository.save_many(
            records
        )

    assert repository.count() == 0


def test_save_many_is_atomic_on_duplicate_deployment_id(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    records = (
        make_record(
            trace_id="trace-atomic-deployment-001",
            deployment_id="deployment-atomic-shared",
        ),
        make_record(
            trace_id="trace-atomic-deployment-002",
            deployment_id="deployment-atomic-shared",
        ),
    )

    with pytest.raises(
        GovernanceTraceAlreadyExistsError
    ):
        repository.save_many(
            records
        )

    assert repository.count() == 0


def test_list_orders_records_newest_first(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    oldest = make_record(
        trace_id="trace-oldest",
        deployment_id="deployment-oldest",
        created_at=BASE_TIME,
    )

    middle = make_record(
        trace_id="trace-middle",
        deployment_id="deployment-middle",
        created_at=(
            BASE_TIME
            + timedelta(
                minutes=10
            )
        ),
    )

    newest = make_record(
        trace_id="trace-newest",
        deployment_id="deployment-newest",
        created_at=(
            BASE_TIME
            + timedelta(
                minutes=20
            )
        ),
    )

    repository.save_many(
        (
            middle,
            oldest,
            newest,
        )
    )

    assert tuple(repository.list()) == (
        newest,
        middle,
        oldest,
    )


def test_list_uses_trace_id_as_deterministic_tie_breaker(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    records = (
        make_record(
            trace_id="trace-a",
            deployment_id="deployment-a",
        ),
        make_record(
            trace_id="trace-c",
            deployment_id="deployment-c",
        ),
        make_record(
            trace_id="trace-b",
            deployment_id="deployment-b",
        ),
    )

    repository.save_many(
        records
    )

    assert tuple(
        record.trace_id
        for record in repository.list()
    ) == (
        "trace-c",
        "trace-b",
        "trace-a",
    )


def test_list_supports_limit_and_offset(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    records = tuple(
        make_record(
            trace_id=f"trace-page-{index:03d}",
            deployment_id=f"deployment-page-{index:03d}",
            created_at=(
                BASE_TIME
                + timedelta(
                    minutes=index
                )
            ),
        )
        for index in range(5)
    )

    repository.save_many(
        records
    )

    page = repository.list(
        limit=2,
        offset=1,
    )

    assert tuple(
        record.trace_id
        for record in page
    ) == (
        "trace-page-003",
        "trace-page-002",
    )


def test_query_filters_by_service_name(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    repository.save_many(
        (
            make_record(
                trace_id="trace-service-a",
                deployment_id="deployment-service-a",
                service_name="payments-api",
            ),
            make_record(
                trace_id="trace-service-b",
                deployment_id="deployment-service-b",
                service_name="search-api",
            ),
        )
    )

    result = repository.query(
        GovernanceTraceQuery(
            service_name="payments-api"
        )
    )

    assert tuple(
        record.trace_id
        for record in result
    ) == (
        "trace-service-a",
    )


def test_query_filters_by_deployment_id(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    repository.save_many(
        (
            make_record(
                trace_id="trace-deployment-filter-a",
                deployment_id="deployment-filter-a",
            ),
            make_record(
                trace_id="trace-deployment-filter-b",
                deployment_id="deployment-filter-b",
            ),
        )
    )

    result = repository.query(
        GovernanceTraceQuery(
            deployment_id="deployment-filter-b"
        )
    )

    assert tuple(
        record.trace_id
        for record in result
    ) == (
        "trace-deployment-filter-b",
    )


def test_query_combines_multiple_filters(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    repository.save_many(
        (
            make_record(
                trace_id="trace-filter-match",
                deployment_id="deployment-filter-match",
                service_name="payments-api",
                environment="production",
                governance_state="awaiting_approval",
                completed=False,
            ),
            make_record(
                trace_id="trace-filter-environment",
                deployment_id="deployment-filter-environment",
                service_name="payments-api",
                environment="staging",
                governance_state="awaiting_approval",
                completed=False,
            ),
            make_record(
                trace_id="trace-filter-state",
                deployment_id="deployment-filter-state",
                service_name="payments-api",
                environment="production",
                governance_state="succeeded",
                final_status="succeeded",
                completed=True,
            ),
        )
    )

    result = repository.query(
        GovernanceTraceQuery(
            service_name="payments-api",
            environment="production",
            governance_state="awaiting_approval",
            completed=False,
        )
    )

    assert tuple(
        record.trace_id
        for record in result
    ) == (
        "trace-filter-match",
    )


def test_query_filters_created_after_inclusively(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    boundary = (
        BASE_TIME
        + timedelta(
            hours=1
        )
    )

    before = make_record(
        trace_id="trace-before",
        deployment_id="deployment-before",
        created_at=(
            boundary
            - timedelta(
                seconds=1
            )
        ),
    )

    at_boundary = make_record(
        trace_id="trace-at-boundary",
        deployment_id="deployment-at-boundary",
        created_at=boundary,
    )

    after = make_record(
        trace_id="trace-after",
        deployment_id="deployment-after",
        created_at=(
            boundary
            + timedelta(
                seconds=1
            )
        ),
    )

    repository.save_many(
        (
            before,
            at_boundary,
            after,
        )
    )

    result = repository.query(
        GovernanceTraceQuery(
            created_after=boundary
        )
    )

    assert tuple(
        record.trace_id
        for record in result
    ) == (
        "trace-after",
        "trace-at-boundary",
    )


def test_query_filters_created_before_inclusively(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    boundary = (
        BASE_TIME
        + timedelta(
            hours=1
        )
    )

    before = make_record(
        trace_id="trace-before-boundary",
        deployment_id="deployment-before-boundary",
        created_at=(
            boundary
            - timedelta(
                seconds=1
            )
        ),
    )

    at_boundary = make_record(
        trace_id="trace-exact-boundary",
        deployment_id="deployment-exact-boundary",
        created_at=boundary,
    )

    after = make_record(
        trace_id="trace-after-boundary",
        deployment_id="deployment-after-boundary",
        created_at=(
            boundary
            + timedelta(
                seconds=1
            )
        ),
    )

    repository.save_many(
        (
            before,
            at_boundary,
            after,
        )
    )

    result = repository.query(
        GovernanceTraceQuery(
            created_before=boundary
        )
    )

    assert tuple(
        record.trace_id
        for record in result
    ) == (
        "trace-exact-boundary",
        "trace-before-boundary",
    )


def test_count_uses_filters_but_ignores_query_pagination(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    records = tuple(
        make_record(
            trace_id=f"trace-count-{index:03d}",
            deployment_id=f"deployment-count-{index:03d}",
            environment=(
                "production"
                if index < 4
                else "staging"
            ),
            created_at=(
                BASE_TIME
                + timedelta(
                    minutes=index
                )
            ),
        )
        for index in range(6)
    )

    repository.save_many(
        records
    )

    query = GovernanceTraceQuery(
        environment="production",
        limit=1,
        offset=2,
    )

    assert len(
        repository.query(
            query
        )
    ) == 1

    assert (
        repository.count(
            query
        )
        == 4
    )


def test_statistics_report_repository_state(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    repository.save_many(
        (
            make_record(
                trace_id="trace-stats-001",
                deployment_id="deployment-stats-001",
                environment="production",
                governance_state="succeeded",
                final_status="succeeded",
                completed=True,
            ),
            make_record(
                trace_id="trace-stats-002",
                deployment_id="deployment-stats-002",
                environment="production",
                governance_state="awaiting_approval",
                completed=False,
            ),
            make_record(
                trace_id="trace-stats-003",
                deployment_id="deployment-stats-003",
                environment="staging",
                governance_state="failed",
                final_status="failed",
                completed=True,
            ),
        )
    )

    statistics = repository.statistics()

    assert statistics.total_traces == 3
    assert statistics.completed_traces == 2
    assert statistics.active_traces == 1
    assert statistics.succeeded_traces == 1
    assert statistics.failed_traces == 1
    assert statistics.blocked_traces == 0
    assert statistics.rejected_traces == 0


def test_delete_removes_existing_record(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    record = make_record(
        trace_id="trace-delete",
        deployment_id="deployment-delete",
    )

    repository.save(
        record
    )

    assert (
        repository.delete(
            record.trace_id
        )
        is True
    )

    assert (
        repository.get_by_trace_id(
            record.trace_id
        )
        is None
    )

    assert repository.count() == 0


def test_delete_missing_record_returns_false(
    repository: DeploymentGovernanceTraceRepository,
) -> None:
    assert (
        repository.delete(
            "missing-trace"
        )
        is False
    )
