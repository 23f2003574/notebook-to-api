import json
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import Mock

import pytest

from backend.observability.deployment_governance_logging import (
    GovernanceLogEntry,
)
from backend.observability.deployment_governance_logging_bootstrap import (
    GovernanceLoggingBootstrap,
    GovernanceLoggingBootstrapHealth,
    build_integrity_logging_bootstrap,
)
from backend.observability.deployment_governance_delivery_runtime import (
    GovernanceIntegrityDeliveryRuntime,
)
from backend.observability.deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_bootstrap,
    run_deployment_governance_logging_health,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _persistence_runtime():
    return build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )


class TestGovernanceLoggingBootstrapConstruction:

    def test_rejects_none_persistence_runtime(self):
        with pytest.raises(ValueError):
            GovernanceLoggingBootstrap(None)


class TestGovernanceLoggingBootstrapInitialization:

    def test_build_wires_every_component(self):
        bootstrap = GovernanceLoggingBootstrap(
            _persistence_runtime()
        ).build()

        assert bootstrap.log_config_service is not None
        assert bootstrap.logger is not None
        assert bootstrap.log_repository is not None
        assert bootstrap.batcher is not None
        assert bootstrap.sampling_service is not None
        assert bootstrap.redaction_service is not None
        assert bootstrap.log_rotation_service is not None
        assert bootstrap.context_service is not None
        assert bootstrap.correlation_service is not None
        assert bootstrap.search_service is not None
        assert bootstrap.export_service is not None
        assert bootstrap.replay_service is not None

    def test_build_twice_raises(self):
        bootstrap = GovernanceLoggingBootstrap(
            _persistence_runtime()
        ).build()

        with pytest.raises(RuntimeError):
            bootstrap.build()

    def test_initialize_before_build_raises(self):
        bootstrap = GovernanceLoggingBootstrap(_persistence_runtime())

        with pytest.raises(RuntimeError):
            bootstrap.initialize()

    def test_initialize_twice_raises(self):
        bootstrap = GovernanceLoggingBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.initialize()

        with pytest.raises(RuntimeError):
            bootstrap.initialize()

    def test_build_returns_self(self):
        bootstrap = GovernanceLoggingBootstrap(_persistence_runtime())

        assert bootstrap.build() is bootstrap

    def test_free_function_builds_but_does_not_initialize(self):
        bootstrap = build_integrity_logging_bootstrap(
            _persistence_runtime()
        )

        assert bootstrap.health().built is True
        assert bootstrap.health().initialized is False


class TestGovernanceLoggingBootstrapDependencyValidation:

    def test_missing_dependency_raises_during_build(self):
        class _BrokenPersistenceRuntime:
            def build_integrity_log_config_service(self):
                return None

            def __getattr__(self, name):
                # Every other build_integrity_log_* accessor
                # returns a harmless object so only
                # log_config_service triggers the failure.
                return lambda **kwargs: object()

        bootstrap = GovernanceLoggingBootstrap(
            _BrokenPersistenceRuntime()
        )

        with pytest.raises(RuntimeError) as excinfo:
            bootstrap.build()

        assert "log_config_service" in str(excinfo.value)

    def test_health_reports_dependencies_valid_after_build(self):
        bootstrap = GovernanceLoggingBootstrap(
            _persistence_runtime()
        ).build()

        assert bootstrap.health().dependencies_valid is True

    def test_health_reports_dependencies_invalid_before_build(self):
        bootstrap = GovernanceLoggingBootstrap(_persistence_runtime())

        assert bootstrap.health().dependencies_valid is False


class TestGovernanceLoggingBootstrapSingletonBehavior:

    def test_two_bootstraps_share_the_same_underlying_logger(self):
        runtime = _persistence_runtime()

        first = GovernanceLoggingBootstrap(runtime).build()
        second = GovernanceLoggingBootstrap(runtime).build()

        assert first.logger is second.logger
        assert first.log_repository is second.log_repository
        assert first.batcher is second.batcher

    def test_bootstrap_services_match_persistence_runtime_singletons(
        self,
    ):
        runtime = _persistence_runtime()

        bootstrap = GovernanceLoggingBootstrap(runtime).build()

        assert bootstrap.logger is runtime.build_integrity_logger()
        assert (
            bootstrap.log_repository
            is runtime.build_integrity_log_repository()
        )

    def test_logging_visible_through_either_bootstrap_instance(self):
        runtime = _persistence_runtime()

        first = GovernanceLoggingBootstrap(runtime).build()
        second = GovernanceLoggingBootstrap(runtime).build()

        first.logger.info("metrics", "record_success")

        assert second.logger.buffered_count() == 1


class TestGovernanceLoggingBootstrapShutdownFlush:

    def test_shutdown_flushes_pending_batch_entries(self):
        runtime = _persistence_runtime()

        bootstrap = GovernanceLoggingBootstrap(runtime).build()

        bootstrap.initialize()

        bootstrap.logger.set_batcher(bootstrap.batcher)

        bootstrap.logger.info("metrics", "buffered_before_shutdown")

        assert bootstrap.batcher.pending_count() == 1
        assert bootstrap.log_repository.list() == ()

        bootstrap.shutdown()

        assert bootstrap.batcher.pending_count() == 0
        events = [e.event for e in bootstrap.log_repository.list()]
        assert "buffered_before_shutdown" in events

    def test_shutdown_before_initialize_is_safe(self):
        bootstrap = GovernanceLoggingBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.shutdown()

    def test_shutdown_before_build_is_safe(self):
        bootstrap = GovernanceLoggingBootstrap(_persistence_runtime())

        bootstrap.shutdown()

    def test_shutdown_twice_is_safe(self):
        bootstrap = GovernanceLoggingBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.initialize()

        bootstrap.shutdown()
        bootstrap.shutdown()

    def test_shutdown_marks_uninitialized(self):
        bootstrap = GovernanceLoggingBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.initialize()
        bootstrap.shutdown()

        assert bootstrap.health().initialized is False


class TestGovernanceLoggingBootstrapHealth:

    def test_health_to_dict(self):
        health = GovernanceLoggingBootstrapHealth(
            built=True,
            initialized=True,
            dependencies_valid=True,
            pending_batch_count=3,
            buffered_entry_count=7,
        )

        assert health.to_dict() == {
            "built": True,
            "initialized": True,
            "dependencies_valid": True,
            "pending_batch_count": 3,
            "buffered_entry_count": 7,
        }

    def test_health_before_anything(self):
        bootstrap = GovernanceLoggingBootstrap(_persistence_runtime())

        health = bootstrap.health()

        assert health.built is False
        assert health.initialized is False
        assert health.pending_batch_count == 0
        assert health.buffered_entry_count == 0

    def test_health_reflects_buffered_entries(self):
        bootstrap = GovernanceLoggingBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.initialize()

        bootstrap.logger.info("metrics", "record_success")

        assert bootstrap.health().buffered_entry_count == 1


class TestGovernanceLoggingBootstrapConfigApplied:

    def test_initialize_applies_minimum_level(self, monkeypatch):
        monkeypatch.setenv(
            "NOTEBOOK2API_GOVERNANCE_LOG_MINIMUM_LEVEL", "ERROR"
        )

        bootstrap = GovernanceLoggingBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.initialize()

        bootstrap.logger.info("metrics", "filtered_out")

        assert bootstrap.log_repository.list() == ()

        bootstrap.logger.error("metrics", "kept")

        assert len(bootstrap.log_repository.list()) == 1


class TestGovernanceLoggingBootstrapRuntimeIntegration:

    def test_logging_bootstrap_backfills_delivery_runtime(self):
        persistence_runtime = _persistence_runtime()

        logging_bootstrap = build_integrity_logging_bootstrap(
            persistence_runtime
        )

        logging_bootstrap.initialize()

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            logging_bootstrap=logging_bootstrap,
        )

        assert runtime.logger is logging_bootstrap.logger
        assert runtime.log_repository is logging_bootstrap.log_repository
        assert runtime.batcher is logging_bootstrap.batcher
        assert (
            runtime.log_config_service
            is logging_bootstrap.log_config_service
        )
        assert runtime.logging_bootstrap() is logging_bootstrap

    def test_explicit_value_overrides_bootstrap_backfill(self):
        persistence_runtime = _persistence_runtime()

        logging_bootstrap = build_integrity_logging_bootstrap(
            persistence_runtime
        )

        explicit_logger = Mock()

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            logger=explicit_logger,
            logging_bootstrap=logging_bootstrap,
        )

        assert runtime.logger is explicit_logger

    def test_runtime_without_logging_bootstrap_defaults_to_none(self):
        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
        )

        assert runtime.logging_bootstrap() is None


class TestGovernanceLoggingBootstrapCli:

    def test_bootstrap_runner_succeeds(self):
        stdout = StringIO()

        exit_code = run_deployment_governance_logging_bootstrap(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["built"] is True
        assert payload["initialized"] is True
        assert payload["dependencies_valid"] is True

    def test_health_runner_succeeds(self):
        stdout = StringIO()

        exit_code = run_deployment_governance_logging_health(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["built"] is True
        assert payload["dependencies_valid"] is True

    def test_bootstrap_runner_human_output(self):
        stdout = StringIO()

        exit_code = run_deployment_governance_logging_bootstrap(
            stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0
        assert "Governance Logging Bootstrap" in stdout.getvalue()
        assert "Built: True" in stdout.getvalue()

    def test_health_runner_human_output(self):
        stdout = StringIO()

        exit_code = run_deployment_governance_logging_health(
            stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0
        assert "Governance Logging Health" in stdout.getvalue()
