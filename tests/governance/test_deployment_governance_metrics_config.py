import pytest

from backend.observability.deployment_governance_metrics_config import (
    DEFAULT_AUTO_FLUSH,
    DEFAULT_COLLECTION_INTERVAL_SECONDS,
    DEFAULT_MAX_HISTORY_AGE_DAYS,
    DEFAULT_MAX_HISTORY_ENTRIES,
    GovernanceIntegrityMetricsConfig,
    GovernanceIntegrityMetricsConfigService,
)


class TestGovernanceIntegrityMetricsConfigDefaults:

    def test_default_construction_uses_sensible_defaults(self):
        config = GovernanceIntegrityMetricsConfig()

        assert (
            config.collection_interval_seconds
            == DEFAULT_COLLECTION_INTERVAL_SECONDS
        )
        assert config.max_history_entries == DEFAULT_MAX_HISTORY_ENTRIES
        assert (
            config.max_history_age_days == DEFAULT_MAX_HISTORY_AGE_DAYS
        )
        assert config.auto_flush == DEFAULT_AUTO_FLUSH

    def test_from_env_with_no_variables_set_uses_defaults(self):
        config = GovernanceIntegrityMetricsConfig.from_env(environ={})

        assert (
            config.collection_interval_seconds
            == DEFAULT_COLLECTION_INTERVAL_SECONDS
        )
        assert config.max_history_entries == DEFAULT_MAX_HISTORY_ENTRIES
        assert (
            config.max_history_age_days == DEFAULT_MAX_HISTORY_AGE_DAYS
        )
        assert config.auto_flush == DEFAULT_AUTO_FLUSH


class TestGovernanceIntegrityMetricsConfigValidation:

    def test_rejects_non_positive_collection_interval(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsConfig(
                collection_interval_seconds=0
            )

        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsConfig(
                collection_interval_seconds=-1
            )

    def test_rejects_negative_max_history_entries(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsConfig(max_history_entries=-1)

    def test_allows_zero_max_history_entries(self):
        config = GovernanceIntegrityMetricsConfig(
            max_history_entries=0
        )

        assert config.max_history_entries == 0

    def test_rejects_non_positive_max_history_age_days(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsConfig(max_history_age_days=0)

        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsConfig(max_history_age_days=-1)

    def test_config_is_immutable(self):
        config = GovernanceIntegrityMetricsConfig()

        with pytest.raises(Exception):
            config.collection_interval_seconds = 120


class TestGovernanceIntegrityMetricsConfigFromEnv:

    def test_reads_collection_interval_from_env(self):
        config = GovernanceIntegrityMetricsConfig.from_env(
            environ={
                "NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS": (
                    "120"
                ),
            }
        )

        assert config.collection_interval_seconds == 120

    def test_reads_max_history_entries_from_env(self):
        config = GovernanceIntegrityMetricsConfig.from_env(
            environ={
                "NOTEBOOK2API_GOVERNANCE_METRICS_MAX_HISTORY_ENTRIES": (
                    "1000"
                ),
            }
        )

        assert config.max_history_entries == 1000

    def test_reads_max_history_age_days_from_env(self):
        config = GovernanceIntegrityMetricsConfig.from_env(
            environ={
                "NOTEBOOK2API_GOVERNANCE_METRICS_MAX_HISTORY_AGE_DAYS": (
                    "7"
                ),
            }
        )

        assert config.max_history_age_days == 7

    def test_reads_auto_flush_from_env(self):
        config = GovernanceIntegrityMetricsConfig.from_env(
            environ={
                "NOTEBOOK2API_GOVERNANCE_METRICS_AUTO_FLUSH": "false",
            }
        )

        assert config.auto_flush is False

    def test_invalid_boolean_env_value_raises_error(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsConfig.from_env(
                environ={
                    "NOTEBOOK2API_GOVERNANCE_METRICS_AUTO_FLUSH": (
                        "maybe"
                    ),
                }
            )

    def test_invalid_env_value_propagates_config_validation(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsConfig.from_env(
                environ={
                    "NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS": (
                        "0"
                    ),
                }
            )


class TestGovernanceIntegrityMetricsConfigServiceLoad:

    def test_load_returns_env_sourced_config(self):
        service = GovernanceIntegrityMetricsConfigService(
            environ={
                "NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS": (
                    "30"
                ),
            }
        )

        config = service.load()

        assert config.collection_interval_seconds == 30

    def test_load_returns_defaults_with_no_environ(self):
        service = GovernanceIntegrityMetricsConfigService(environ={})

        config = service.load()

        assert (
            config.collection_interval_seconds
            == DEFAULT_COLLECTION_INTERVAL_SECONDS
        )


class TestGovernanceIntegrityMetricsConfigServiceReload:

    def test_reload_picks_up_new_environ_values(self):
        environ = {
            "NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS": (
                "30"
            ),
        }

        service = GovernanceIntegrityMetricsConfigService(
            environ=environ
        )

        assert service.load().collection_interval_seconds == 30

        environ[
            "NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS"
        ] = "90"

        reloaded = service.reload()

        assert reloaded.collection_interval_seconds == 90
        assert service.load().collection_interval_seconds == 90

    def test_reload_without_restart_reflected_immediately(self):
        environ = {}

        service = GovernanceIntegrityMetricsConfigService(
            environ=environ
        )

        environ[
            "NOTEBOOK2API_GOVERNANCE_METRICS_MAX_HISTORY_ENTRIES"
        ] = "42"

        service.reload()

        assert service.load().max_history_entries == 42


class TestGovernanceIntegrityMetricsConfigServiceUpdate:

    def test_update_applies_override_on_top_of_current_config(self):
        service = GovernanceIntegrityMetricsConfigService(environ={})

        updated = service.update(collection_interval_seconds=45)

        assert updated.collection_interval_seconds == 45
        assert service.load().collection_interval_seconds == 45
        # Untouched fields carry over from the previous config.
        assert updated.max_history_entries == DEFAULT_MAX_HISTORY_ENTRIES

    def test_update_with_invalid_value_raises_and_does_not_apply(
        self,
    ):
        service = GovernanceIntegrityMetricsConfigService(environ={})

        with pytest.raises(ValueError):
            service.update(collection_interval_seconds=-5)

        # State is unchanged after a rejected update.
        assert (
            service.load().collection_interval_seconds
            == DEFAULT_COLLECTION_INTERVAL_SECONDS
        )

    def test_update_multiple_fields_at_once(self):
        service = GovernanceIntegrityMetricsConfigService(environ={})

        updated = service.update(
            max_history_entries=10, auto_flush=False
        )

        assert updated.max_history_entries == 10
        assert updated.auto_flush is False


class TestGovernanceIntegrityMetricsConfigServiceValidate:

    def test_validate_returns_candidate_without_applying(self):
        service = GovernanceIntegrityMetricsConfigService(environ={})

        candidate = service.validate(collection_interval_seconds=999)

        assert candidate.collection_interval_seconds == 999
        # The service's own held config is untouched.
        assert (
            service.load().collection_interval_seconds
            == DEFAULT_COLLECTION_INTERVAL_SECONDS
        )

    def test_validate_raises_for_invalid_candidate(self):
        service = GovernanceIntegrityMetricsConfigService(environ={})

        with pytest.raises(ValueError):
            service.validate(max_history_age_days=0)
