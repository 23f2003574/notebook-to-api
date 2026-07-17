from __future__ import annotations

import pytest

from backend.observability.deployment_governance_failure_policy import (
    GovernanceIntegrityFailureAction,
    GovernanceIntegrityFailurePolicy,
    GovernanceIntegrityFailurePolicyService,
    InMemoryGovernanceIntegrityFailurePolicyRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_failure_policy import (
    SQLiteGovernanceIntegrityFailurePolicyRepository,
)


class Harness:
    def __init__(self) -> None:
        self.repository = (
            InMemoryGovernanceIntegrityFailurePolicyRepository()
        )

        self.service = GovernanceIntegrityFailurePolicyService(
            self.repository
        )


# --- Model -------------------------------------------------------------


def test_policy_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        GovernanceIntegrityFailurePolicy(
            name="  ",
            action=GovernanceIntegrityFailureAction.RETRY,
            max_retry_attempts=1,
        )


def test_policy_rejects_negative_max_retry_attempts() -> None:
    with pytest.raises(
        ValueError,
        match="max_retry_attempts must be greater than or equal to zero",
    ):
        GovernanceIntegrityFailurePolicy(
            name="default",
            action=GovernanceIntegrityFailureAction.RETRY,
            max_retry_attempts=-1,
        )


def test_policy_allows_zero_max_retry_attempts() -> None:
    policy = GovernanceIntegrityFailurePolicy(
        name="default",
        action=GovernanceIntegrityFailureAction.IGNORE,
        max_retry_attempts=0,
    )

    assert policy.max_retry_attempts == 0


# --- Model: resolve --------------------------------------------------------


def test_resolve_retries_while_budget_remains() -> None:
    policy = GovernanceIntegrityFailurePolicy(
        name="default",
        action=GovernanceIntegrityFailureAction.DEAD_LETTER,
        max_retry_attempts=2,
    )

    assert (
        policy.resolve(0) is GovernanceIntegrityFailureAction.RETRY
    )
    assert (
        policy.resolve(1) is GovernanceIntegrityFailureAction.RETRY
    )


def test_resolve_falls_back_to_action_once_exhausted() -> None:
    policy = GovernanceIntegrityFailurePolicy(
        name="default",
        action=GovernanceIntegrityFailureAction.DEAD_LETTER,
        max_retry_attempts=2,
    )

    assert (
        policy.resolve(2)
        is GovernanceIntegrityFailureAction.DEAD_LETTER
    )
    assert (
        policy.resolve(3)
        is GovernanceIntegrityFailureAction.DEAD_LETTER
    )


def test_resolve_with_zero_budget_never_retries() -> None:
    policy = GovernanceIntegrityFailurePolicy(
        name="default",
        action=GovernanceIntegrityFailureAction.IGNORE,
        max_retry_attempts=0,
    )

    assert (
        policy.resolve(0) is GovernanceIntegrityFailureAction.IGNORE
    )


# --- Repository ----------------------------------------------------------


def test_repository_save_and_get() -> None:
    repository = InMemoryGovernanceIntegrityFailurePolicyRepository()

    policy = GovernanceIntegrityFailurePolicy(
        name="default",
        action=GovernanceIntegrityFailureAction.RETRY,
        max_retry_attempts=1,
    )

    repository.save(policy)

    assert repository.get("default") == policy


def test_repository_get_missing_returns_none() -> None:
    repository = InMemoryGovernanceIntegrityFailurePolicyRepository()

    assert repository.get("missing") is None


def test_repository_list_orders_by_name() -> None:
    repository = InMemoryGovernanceIntegrityFailurePolicyRepository()

    repository.save(
        GovernanceIntegrityFailurePolicy(
            name="zeta",
            action=GovernanceIntegrityFailureAction.IGNORE,
            max_retry_attempts=0,
        )
    )
    repository.save(
        GovernanceIntegrityFailurePolicy(
            name="alpha",
            action=GovernanceIntegrityFailureAction.IGNORE,
            max_retry_attempts=0,
        )
    )

    names = [policy.name for policy in repository.list()]

    assert names == ["alpha", "zeta"]


def test_repository_save_overwrites_existing() -> None:
    repository = InMemoryGovernanceIntegrityFailurePolicyRepository()

    repository.save(
        GovernanceIntegrityFailurePolicy(
            name="default",
            action=GovernanceIntegrityFailureAction.IGNORE,
            max_retry_attempts=1,
        )
    )
    repository.save(
        GovernanceIntegrityFailurePolicy(
            name="default",
            action=GovernanceIntegrityFailureAction.RETRY,
            max_retry_attempts=3,
        )
    )

    policy = repository.get("default")

    assert policy is not None
    assert policy.action is GovernanceIntegrityFailureAction.RETRY
    assert policy.max_retry_attempts == 3


def test_repository_delete_removes_policy() -> None:
    repository = InMemoryGovernanceIntegrityFailurePolicyRepository()

    repository.save(
        GovernanceIntegrityFailurePolicy(
            name="default",
            action=GovernanceIntegrityFailureAction.IGNORE,
            max_retry_attempts=0,
        )
    )

    repository.delete("default")

    assert repository.get("default") is None


def test_repository_delete_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityFailurePolicyRepository()

    with pytest.raises(KeyError):
        repository.delete("missing")


def test_repository_exists() -> None:
    repository = InMemoryGovernanceIntegrityFailurePolicyRepository()

    assert repository.exists("default") is False

    repository.save(
        GovernanceIntegrityFailurePolicy(
            name="default",
            action=GovernanceIntegrityFailureAction.IGNORE,
            max_retry_attempts=0,
        )
    )

    assert repository.exists("default") is True


# --- Service: create -----------------------------------------------------


def test_create_returns_policy_with_retry_action() -> None:
    harness = Harness()

    policy = harness.service.create(
        "default", GovernanceIntegrityFailureAction.RETRY, 2
    )

    assert policy.action is GovernanceIntegrityFailureAction.RETRY


def test_create_rejects_duplicate_name() -> None:
    harness = Harness()

    harness.service.create(
        "default", GovernanceIntegrityFailureAction.RETRY, 2
    )

    with pytest.raises(ValueError):
        harness.service.create(
            "default", GovernanceIntegrityFailureAction.IGNORE, 0
        )


# --- Service: update -----------------------------------------------------


def test_update_changes_max_retry_attempts() -> None:
    harness = Harness()

    harness.service.create(
        "default", GovernanceIntegrityFailureAction.DEAD_LETTER, 2
    )

    updated = harness.service.update(
        "default", max_retry_attempts=5
    )

    assert updated.max_retry_attempts == 5
    assert updated.action is GovernanceIntegrityFailureAction.DEAD_LETTER

    persisted = harness.service.get("default")

    assert persisted is not None
    assert persisted.max_retry_attempts == 5


def test_update_changes_action() -> None:
    harness = Harness()

    harness.service.create(
        "default", GovernanceIntegrityFailureAction.RETRY, 2
    )

    updated = harness.service.update(
        "default", action=GovernanceIntegrityFailureAction.IGNORE
    )

    assert updated.action is GovernanceIntegrityFailureAction.IGNORE
    assert updated.max_retry_attempts == 2


def test_update_missing_raises_lookup_error() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.service.update("missing", max_retry_attempts=1)


# --- Service: delete/get/list ---------------------------------------------


def test_delete_removes_policy() -> None:
    harness = Harness()

    harness.service.create(
        "default", GovernanceIntegrityFailureAction.IGNORE, 0
    )

    harness.service.delete("default")

    assert harness.service.get("default") is None


def test_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.delete("missing")


def test_list_returns_created_policies() -> None:
    harness = Harness()

    harness.service.create(
        "default", GovernanceIntegrityFailureAction.IGNORE, 0
    )
    harness.service.create(
        "aggressive", GovernanceIntegrityFailureAction.RETRY, 5
    )

    names = [policy.name for policy in harness.service.list()]

    assert names == ["aggressive", "default"]


# --- SQLite repository -----------------------------------------------------


def test_sqlite_repository_persists_and_survives_reload(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "failure-policy.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityFailurePolicyRepository(
        database
    )

    repository.save(
        GovernanceIntegrityFailurePolicy(
            name="default",
            action=GovernanceIntegrityFailureAction.DEAD_LETTER,
            max_retry_attempts=2,
        )
    )

    reloaded_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    reloaded_repository = SQLiteGovernanceIntegrityFailurePolicyRepository(
        reloaded_database
    )

    policy = reloaded_repository.get("default")

    assert policy is not None
    assert policy.action is GovernanceIntegrityFailureAction.DEAD_LETTER
    assert policy.max_retry_attempts == 2


def test_sqlite_repository_update_persists(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "failure-policy-update.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityFailurePolicyRepository(
        database
    )

    repository.save(
        GovernanceIntegrityFailurePolicy(
            name="default",
            action=GovernanceIntegrityFailureAction.RETRY,
            max_retry_attempts=1,
        )
    )
    repository.save(
        GovernanceIntegrityFailurePolicy(
            name="default",
            action=GovernanceIntegrityFailureAction.RETRY,
            max_retry_attempts=3,
        )
    )

    policy = repository.get("default")

    assert policy is not None
    assert policy.max_retry_attempts == 3


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_policy_service_over_sqlite(
    tmp_path,
) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "policy-runtime.db"
        )
    )

    service = runtime.build_integrity_failure_policy_service()

    service.create(
        "default", GovernanceIntegrityFailureAction.DEAD_LETTER, 2
    )

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "policy-runtime.db"
        )
    )

    reloaded_service = (
        reloaded_runtime.build_integrity_failure_policy_service()
    )

    policy = reloaded_service.get("default")

    assert policy is not None
    assert policy.action is GovernanceIntegrityFailureAction.DEAD_LETTER
