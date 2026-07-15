from __future__ import annotations

import pytest

from backend.observability.deployment_governance_audit_retention import (
    GovernanceIntegrityAuditAutomaticRetentionConfig,
    GovernanceIntegrityAuditRetentionPolicy,
    governance_integrity_audit_automatic_retention_config_from_env,
)


def test_disabled_config_is_permissive_by_default() -> None:
    config = GovernanceIntegrityAuditAutomaticRetentionConfig()

    assert config.enabled is False
    assert config.max_records is None
    assert config.max_age_days is None


def test_disabled_config_tolerates_stray_limits() -> None:
    # Dormant configuration should never raise merely because a limit is
    # set; validation only kicks in once enabled=True.
    config = GovernanceIntegrityAuditAutomaticRetentionConfig(
        enabled=False, max_records=1
    )

    assert config.enabled is False


def test_enabled_config_requires_at_least_one_limit() -> None:
    with pytest.raises(
        ValueError, match="at least one retention limit"
    ):
        GovernanceIntegrityAuditAutomaticRetentionConfig(enabled=True)


def test_enabled_config_requires_max_records_at_least_two() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        GovernanceIntegrityAuditAutomaticRetentionConfig(
            enabled=True, max_records=1
        )


def test_enabled_config_accepts_max_records_of_two() -> None:
    config = GovernanceIntegrityAuditAutomaticRetentionConfig(
        enabled=True, max_records=2
    )

    assert config.max_records == 2


def test_enabled_config_rejects_non_positive_max_age_days() -> None:
    with pytest.raises(
        ValueError, match="max_age_days must be greater than zero"
    ):
        GovernanceIntegrityAuditAutomaticRetentionConfig(
            enabled=True, max_age_days=0
        )


def test_manual_retention_policy_can_still_keep_one_record() -> None:
    # Manual pruning is an explicit operator decision and is not subject
    # to the automatic-retention minimum-history invariant.
    policy = GovernanceIntegrityAuditRetentionPolicy(max_records=1)

    assert policy.max_records == 1


def test_disabled_classmethod() -> None:
    config = GovernanceIntegrityAuditAutomaticRetentionConfig.disabled()

    assert config.enabled is False


def test_to_policy_requires_enabled() -> None:
    config = GovernanceIntegrityAuditAutomaticRetentionConfig.disabled()

    with pytest.raises(
        ValueError, match="disabled automatic retention"
    ):
        config.to_policy()


def test_to_policy_converts_enabled_config() -> None:
    config = GovernanceIntegrityAuditAutomaticRetentionConfig(
        enabled=True,
        max_records=100,
        max_age_days=30,
        preserve_latest=False,
    )

    policy = config.to_policy()

    assert policy.max_records == 100
    assert policy.max_age_days == 30
    assert policy.preserve_latest is False


def test_automatic_retention_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_ENABLED",
        raising=False,
    )

    config = (
        governance_integrity_audit_automatic_retention_config_from_env()
    )

    assert config.enabled is False


def test_automatic_retention_loads_max_records_from_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_ENABLED", "true"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_MAX_RECORDS", "100"
    )

    config = (
        governance_integrity_audit_automatic_retention_config_from_env()
    )

    assert config.enabled is True
    assert config.max_records == 100


def test_automatic_retention_loads_max_age_days_from_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_ENABLED", "true"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_MAX_AGE_DAYS", "180"
    )

    config = (
        governance_integrity_audit_automatic_retention_config_from_env()
    )

    assert config.enabled is True
    assert config.max_age_days == 180


def test_automatic_retention_loads_preserve_latest_from_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_ENABLED", "true"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_MAX_RECORDS", "10"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_PRESERVE_LATEST", "false"
    )

    config = (
        governance_integrity_audit_automatic_retention_config_from_env()
    )

    assert config.preserve_latest is False


def test_enabled_automatic_retention_requires_a_limit(monkeypatch) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_ENABLED", "true"
    )
    monkeypatch.delenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_MAX_RECORDS",
        raising=False,
    )
    monkeypatch.delenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_MAX_AGE_DAYS",
        raising=False,
    )

    with pytest.raises(
        ValueError, match="at least one retention limit"
    ):
        governance_integrity_audit_automatic_retention_config_from_env()


@pytest.mark.parametrize(
    "value", ["true", "TRUE", "1", "yes", "on"]
)
def test_boolean_parsing_accepts_true_values(monkeypatch, value) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_ENABLED", value
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_MAX_RECORDS", "10"
    )

    config = (
        governance_integrity_audit_automatic_retention_config_from_env()
    )

    assert config.enabled is True


@pytest.mark.parametrize(
    "value", ["false", "FALSE", "0", "no", "off"]
)
def test_boolean_parsing_accepts_false_values(monkeypatch, value) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_ENABLED", value
    )

    config = (
        governance_integrity_audit_automatic_retention_config_from_env()
    )

    assert config.enabled is False


def test_invalid_retention_enabled_boolean_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_ENABLED", "maybe"
    )

    with pytest.raises(ValueError, match="must be a boolean value"):
        governance_integrity_audit_automatic_retention_config_from_env()


def test_invalid_max_records_integer_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_MAX_RECORDS", "not-a-number"
    )

    with pytest.raises(ValueError, match="must be an integer"):
        governance_integrity_audit_automatic_retention_config_from_env()


def test_non_positive_max_records_integer_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_AUDIT_RETENTION_MAX_RECORDS", "0"
    )

    with pytest.raises(
        ValueError, match="must be greater than zero"
    ):
        governance_integrity_audit_automatic_retention_config_from_env()
