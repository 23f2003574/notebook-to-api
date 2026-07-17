from __future__ import annotations

import pytest

from backend.observability.deployment_governance_delivery_policies import (
    GovernanceIntegrityDeliveryPolicy,
    GovernanceIntegrityDeliveryPolicyService,
    InMemoryGovernanceIntegrityDeliveryPolicyRepository,
)
from backend.observability.deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelService,
    GovernanceIntegrityNotificationChannelType,
    InMemoryGovernanceIntegrityNotificationChannelRepository,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.sqlite_deployment_governance_delivery_policies import (
    SQLiteGovernanceIntegrityDeliveryPolicyRepository,
)


class Harness:
    def __init__(self) -> None:
        self.channel_repository = (
            InMemoryGovernanceIntegrityNotificationChannelRepository()
        )

        self.channel_service = GovernanceIntegrityNotificationChannelService(
            self.channel_repository
        )

        self.repository = (
            InMemoryGovernanceIntegrityDeliveryPolicyRepository()
        )

        self.service = GovernanceIntegrityDeliveryPolicyService(
            self.repository, self.channel_service
        )

    def add_channel(
        self,
        name: str,
        channel_type: GovernanceIntegrityNotificationChannelType = (
            GovernanceIntegrityNotificationChannelType.EMAIL
        ),
    ) -> None:
        self.channel_service.create(name, channel_type, f"dest-{name}")


# --- Model -------------------------------------------------------------


def test_policy_rejects_empty_channel_name() -> None:
    with pytest.raises(ValueError, match="channel_name must not be empty"):
        GovernanceIntegrityDeliveryPolicy(
            channel_name="  ",
            retry_limit=3,
            timeout_seconds=30,
            rate_limit_per_minute=60,
            enabled=True,
        )


def test_policy_rejects_negative_retry_limit() -> None:
    with pytest.raises(
        ValueError,
        match="retry_limit must be greater than or equal to zero",
    ):
        GovernanceIntegrityDeliveryPolicy(
            channel_name="email",
            retry_limit=-1,
            timeout_seconds=30,
            rate_limit_per_minute=60,
            enabled=True,
        )


def test_policy_rejects_non_positive_timeout() -> None:
    with pytest.raises(
        ValueError, match="timeout_seconds must be greater than zero"
    ):
        GovernanceIntegrityDeliveryPolicy(
            channel_name="email",
            retry_limit=3,
            timeout_seconds=0,
            rate_limit_per_minute=60,
            enabled=True,
        )


def test_policy_rejects_non_positive_rate_limit() -> None:
    with pytest.raises(
        ValueError,
        match="rate_limit_per_minute must be greater than zero",
    ):
        GovernanceIntegrityDeliveryPolicy(
            channel_name="email",
            retry_limit=3,
            timeout_seconds=30,
            rate_limit_per_minute=0,
            enabled=True,
        )


def test_policy_allows_zero_retry_limit() -> None:
    policy = GovernanceIntegrityDeliveryPolicy(
        channel_name="email",
        retry_limit=0,
        timeout_seconds=30,
        rate_limit_per_minute=60,
        enabled=True,
    )

    assert policy.retry_limit == 0


# --- Repository ----------------------------------------------------------


def test_repository_save_rejects_duplicate_channel() -> None:
    repository = InMemoryGovernanceIntegrityDeliveryPolicyRepository()

    policy = GovernanceIntegrityDeliveryPolicy(
        channel_name="email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
        enabled=True,
    )

    repository.save(policy)

    with pytest.raises(Exception):
        repository.save(policy)


def test_repository_update_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityDeliveryPolicyRepository()

    with pytest.raises(KeyError):
        repository.update(
            GovernanceIntegrityDeliveryPolicy(
                channel_name="missing",
                retry_limit=3,
                timeout_seconds=30,
                rate_limit_per_minute=60,
                enabled=True,
            )
        )


def test_repository_delete_missing_raises_key_error() -> None:
    repository = InMemoryGovernanceIntegrityDeliveryPolicyRepository()

    with pytest.raises(KeyError):
        repository.delete("missing")


def test_repository_list_orders_by_channel_name() -> None:
    repository = InMemoryGovernanceIntegrityDeliveryPolicyRepository()

    repository.save(
        GovernanceIntegrityDeliveryPolicy(
            channel_name="zeta",
            retry_limit=1,
            timeout_seconds=10,
            rate_limit_per_minute=10,
            enabled=True,
        )
    )
    repository.save(
        GovernanceIntegrityDeliveryPolicy(
            channel_name="alpha",
            retry_limit=1,
            timeout_seconds=10,
            rate_limit_per_minute=10,
            enabled=True,
        )
    )

    names = [policy.channel_name for policy in repository.list()]

    assert names == ["alpha", "zeta"]


# --- Service: create -----------------------------------------------------


def test_create_returns_policy_with_retry_limit() -> None:
    harness = Harness()

    harness.add_channel("email")

    policy = harness.service.create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    assert policy.retry_limit == 3


def test_create_rejects_missing_channel() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.service.create(
            "missing",
            retry_limit=3,
            timeout_seconds=30,
            rate_limit_per_minute=60,
        )


def test_create_rejects_duplicate_policy() -> None:
    harness = Harness()

    harness.add_channel("email")

    harness.service.create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    with pytest.raises(ValueError):
        harness.service.create(
            "email",
            retry_limit=5,
            timeout_seconds=45,
            rate_limit_per_minute=30,
        )


# --- Service: update -----------------------------------------------------


def test_update_changes_timeout() -> None:
    harness = Harness()

    harness.add_channel("email")

    harness.service.create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    updated = harness.service.update("email", timeout_seconds=45)

    assert updated.timeout_seconds == 45
    assert updated.retry_limit == 3

    persisted = harness.service.get("email")

    assert persisted is not None
    assert persisted.timeout_seconds == 45


def test_update_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.update("missing", timeout_seconds=45)


# --- Service: delete/get/list ---------------------------------------------


def test_delete_removes_policy() -> None:
    harness = Harness()

    harness.add_channel("email")

    harness.service.create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    harness.service.delete("email")

    assert harness.service.get("email") is None


def test_delete_missing_raises_key_error() -> None:
    harness = Harness()

    with pytest.raises(KeyError):
        harness.service.delete("missing")


def test_list_returns_created_policies() -> None:
    harness = Harness()

    harness.add_channel("email")
    harness.add_channel(
        "slack", GovernanceIntegrityNotificationChannelType.SLACK
    )

    harness.service.create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )
    harness.service.create(
        "slack",
        retry_limit=1,
        timeout_seconds=10,
        rate_limit_per_minute=20,
    )

    names = [policy.channel_name for policy in harness.service.list()]

    assert names == ["email", "slack"]


# --- Service: resolve ----------------------------------------------------


def test_resolve_returns_expected_policy() -> None:
    harness = Harness()

    harness.add_channel("email")

    created = harness.service.create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    resolved = harness.service.resolve("email")

    assert resolved == created


def test_resolve_missing_raises_lookup_error() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.service.resolve("missing")


# --- SQLite repository -----------------------------------------------------


def test_sqlite_repository_persists_and_survives_reload(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database_path = tmp_path / "delivery-policies.db"

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    repository = SQLiteGovernanceIntegrityDeliveryPolicyRepository(
        database
    )

    repository.save(
        GovernanceIntegrityDeliveryPolicy(
            channel_name="email",
            retry_limit=3,
            timeout_seconds=30,
            rate_limit_per_minute=60,
            enabled=True,
        )
    )

    reloaded_database = SQLiteDatabase(
        SQLiteDatabaseConfig(database_path=database_path)
    )

    reloaded_repository = SQLiteGovernanceIntegrityDeliveryPolicyRepository(
        reloaded_database
    )

    policy = reloaded_repository.get("email")

    assert policy is not None
    assert policy.retry_limit == 3
    assert policy.timeout_seconds == 30
    assert policy.rate_limit_per_minute == 60


def test_sqlite_repository_save_rejects_duplicate(tmp_path) -> None:
    from backend.persistence.sqlite_database import (
        SQLiteDatabase,
        SQLiteDatabaseConfig,
    )

    database = SQLiteDatabase(
        SQLiteDatabaseConfig(
            database_path=tmp_path / "delivery-policies-dup.db"
        )
    )

    repository = SQLiteGovernanceIntegrityDeliveryPolicyRepository(
        database
    )

    policy = GovernanceIntegrityDeliveryPolicy(
        channel_name="email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
        enabled=True,
    )

    repository.save(policy)

    with pytest.raises(Exception):
        repository.save(policy)


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_working_policy_service_over_sqlite(
    tmp_path,
) -> None:
    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "delivery-policies-runtime.db"
        )
    )

    channel_service = runtime.build_integrity_notification_channel_service()
    channel_service.create(
        "email",
        GovernanceIntegrityNotificationChannelType.EMAIL,
        "ops@example.com",
    )

    policy_service = runtime.build_integrity_delivery_policy_service()
    policy_service.create(
        "email",
        retry_limit=3,
        timeout_seconds=30,
        rate_limit_per_minute=60,
    )

    reloaded_runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "delivery-policies-runtime.db"
        )
    )

    reloaded_policy_service = (
        reloaded_runtime.build_integrity_delivery_policy_service()
    )

    policy = reloaded_policy_service.get("email")

    assert policy is not None
    assert policy.retry_limit == 3
