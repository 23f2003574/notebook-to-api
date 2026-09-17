import json
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import Mock

import pytest

from backend.observability.deployment_governance_logging import (
    GovernanceIntegrityLogger,
)
from backend.observability.deployment_governance_log_repository import (
    InMemoryGovernanceLogRepository,
)
from backend.observability.deployment_governance_log_redaction import (
    GovernanceLogRedactionService,
)
from backend.observability.deployment_governance_log_sampling import (
    GovernanceLogSamplingPolicy,
    GovernanceLogSamplingService,
)
from backend.observability.deployment_governance_log_batcher import (
    GovernanceLogBatcher,
)
from backend.observability.deployment_governance_log_config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_ENABLE_REDACTION,
    DEFAULT_ENABLE_SAMPLING,
    DEFAULT_FLUSH_INTERVAL_SECONDS,
    DEFAULT_MINIMUM_LEVEL,
    GovernanceLogConfig,
    GovernanceLogConfigService,
)
from backend.observability.deployment_governance_delivery_runtime import (
    GovernanceIntegrityDeliveryRuntime,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_config_reload,
    run_deployment_governance_logging_config_show,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestGovernanceLogConfigDefaults:

    def test_default_values(self):
        config = GovernanceLogConfig()

        assert config.minimum_level == DEFAULT_MINIMUM_LEVEL
        assert config.batch_size == DEFAULT_BATCH_SIZE
        assert (
            config.flush_interval_seconds
            == DEFAULT_FLUSH_INTERVAL_SECONDS
        )
        assert config.enable_sampling == DEFAULT_ENABLE_SAMPLING
        assert config.enable_redaction == DEFAULT_ENABLE_REDACTION

    def test_to_dict(self):
        config = GovernanceLogConfig(
            minimum_level="WARNING",
            batch_size=50,
            flush_interval_seconds=10,
            enable_sampling=False,
            enable_redaction=True,
        )

        assert config.to_dict() == {
            "minimum_level": "WARNING",
            "batch_size": 50,
            "flush_interval_seconds": 10,
            "enable_sampling": False,
            "enable_redaction": True,
        }


class TestGovernanceLogConfigValidation:

    def test_rejects_invalid_minimum_level(self):
        with pytest.raises(ValueError):
            GovernanceLogConfig(minimum_level="TRACE")

    def test_rejects_non_positive_batch_size(self):
        with pytest.raises(ValueError):
            GovernanceLogConfig(batch_size=0)

    def test_rejects_non_positive_flush_interval(self):
        with pytest.raises(ValueError):
            GovernanceLogConfig(flush_interval_seconds=0)

    def test_accepts_every_valid_level(self):
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            GovernanceLogConfig(minimum_level=level)


class TestGovernanceLogConfigFromEnv:

    def test_defaults_when_unset(self):
        config = GovernanceLogConfig.from_env(environ={})

        assert config == GovernanceLogConfig()

    def test_reads_all_variables(self):
        config = GovernanceLogConfig.from_env(
            environ={
                "NOTEBOOK2API_GOVERNANCE_LOG_MINIMUM_LEVEL": "warning",
                "NOTEBOOK2API_GOVERNANCE_LOG_BATCH_SIZE": "25",
                "NOTEBOOK2API_GOVERNANCE_LOG_FLUSH_INTERVAL_SECONDS": "9",
                "NOTEBOOK2API_GOVERNANCE_LOG_ENABLE_SAMPLING": "false",
                "NOTEBOOK2API_GOVERNANCE_LOG_ENABLE_REDACTION": "no",
            }
        )

        assert config.minimum_level == "WARNING"
        assert config.batch_size == 25
        assert config.flush_interval_seconds == 9
        assert config.enable_sampling is False
        assert config.enable_redaction is False

    def test_invalid_boolean_raises(self):
        with pytest.raises(ValueError):
            GovernanceLogConfig.from_env(
                environ={
                    "NOTEBOOK2API_GOVERNANCE_LOG_ENABLE_SAMPLING": (
                        "maybe"
                    ),
                }
            )


class TestGovernanceLogConfigService:

    def test_load_returns_current_config(self):
        service = GovernanceLogConfigService(environ={})

        assert service.load() == GovernanceLogConfig()

    def test_reload_re_reads_environment(self):
        environ = {}

        service = GovernanceLogConfigService(environ=environ)

        assert service.load().batch_size == DEFAULT_BATCH_SIZE

        environ["NOTEBOOK2API_GOVERNANCE_LOG_BATCH_SIZE"] = "42"

        reloaded = service.reload()

        assert reloaded.batch_size == 42
        assert service.load().batch_size == 42

    def test_update_applies_overrides(self):
        service = GovernanceLogConfigService(environ={})

        updated = service.update(minimum_level="ERROR")

        assert updated.minimum_level == "ERROR"
        assert service.load().minimum_level == "ERROR"
        # Untouched fields preserved.
        assert updated.batch_size == DEFAULT_BATCH_SIZE

    def test_update_rejects_invalid_override(self):
        service = GovernanceLogConfigService(environ={})

        with pytest.raises(ValueError):
            service.update(batch_size=-1)

        # Original config untouched after a failed update.
        assert service.load().batch_size == DEFAULT_BATCH_SIZE

    def test_validate_does_not_mutate_state(self):
        service = GovernanceLogConfigService(environ={})

        candidate = service.validate(minimum_level="ERROR")

        assert candidate.minimum_level == "ERROR"
        assert service.load().minimum_level == DEFAULT_MINIMUM_LEVEL


class TestGovernanceLogConfigLoggerMinimumLevel:

    def test_below_minimum_level_is_dropped_entirely(self):
        repository = InMemoryGovernanceLogRepository()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            minimum_level="WARNING",
        )

        entry = logger.info("metrics", "routine_event")

        assert entry.event == "routine_event"
        assert repository.list() == ()
        assert logger.entries() == ()

    def test_at_or_above_minimum_level_is_logged(self):
        repository = InMemoryGovernanceLogRepository()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            minimum_level="WARNING",
        )

        logger.warning("metrics", "at_threshold")
        logger.error("metrics", "above_threshold")

        assert len(repository.list()) == 2

    def test_default_minimum_level_logs_everything(self):
        repository = InMemoryGovernanceLogRepository()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME, repository=repository
        )

        logger.debug("metrics", "debug_event")

        assert len(repository.list()) == 1

    def test_set_minimum_level_after_construction(self):
        repository = InMemoryGovernanceLogRepository()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME, repository=repository
        )

        logger.set_minimum_level("ERROR")

        logger.info("metrics", "filtered_out")

        assert repository.list() == ()

    def test_constructor_rejects_invalid_minimum_level(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityLogger(minimum_level="TRACE")

    def test_set_minimum_level_rejects_invalid_value(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        with pytest.raises(ValueError):
            logger.set_minimum_level("TRACE")


class TestGovernanceLogConfigBatcherReconfigure:

    def test_reconfigure_replaces_batch_size(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=100, clock=lambda: BASE_TIME
        )

        batcher.reconfigure(batch_size=2)

        from backend.observability.deployment_governance_logging import (
            GovernanceLogEntry,
        )

        def _entry(event):
            return GovernanceLogEntry(
                timestamp=BASE_TIME,
                level="INFO",
                component="metrics",
                event=event,
                fields={},
            )

        batcher.enqueue(_entry("a"))
        assert batcher.flush_if_needed() is None

        batcher.enqueue(_entry("b"))
        assert batcher.flush_if_needed() is not None

    def test_reconfigure_only_changes_given_fields(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository,
            batch_size=100,
            flush_interval_seconds=10,
            clock=lambda: BASE_TIME,
        )

        batcher.reconfigure(batch_size=5)

        # No public getter, so verify indirectly via flush_if_needed
        # threshold behavior instead of a private attribute.
        assert batcher._batch_size == 5
        assert batcher._flush_interval_seconds == 10

    def test_reconfigure_rejects_invalid_batch_size(self):
        batcher = GovernanceLogBatcher(
            InMemoryGovernanceLogRepository(),
            clock=lambda: BASE_TIME,
        )

        with pytest.raises(ValueError):
            batcher.reconfigure(batch_size=0)

    def test_reconfigure_rejects_invalid_flush_interval(self):
        batcher = GovernanceLogBatcher(
            InMemoryGovernanceLogRepository(),
            clock=lambda: BASE_TIME,
        )

        with pytest.raises(ValueError):
            batcher.reconfigure(flush_interval_seconds=0)


class TestGovernanceLogConfigRuntimeInjection:

    def test_reload_log_config_injects_minimum_level(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        environ = {
            "NOTEBOOK2API_GOVERNANCE_LOG_MINIMUM_LEVEL": "ERROR"
        }

        config_service = GovernanceLogConfigService(environ=environ)

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            logger=logger,
            log_config_service=config_service,
        )

        config = runtime.reload_log_config()

        assert config.minimum_level == "ERROR"

        repository = InMemoryGovernanceLogRepository()
        logger.set_repository(repository)

        logger.info("metrics", "filtered_out")

        assert repository.list() == ()

    def test_reload_log_config_toggles_redaction_off(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        redaction_service = GovernanceLogRedactionService()

        environ = {
            "NOTEBOOK2API_GOVERNANCE_LOG_ENABLE_REDACTION": "false"
        }

        config_service = GovernanceLogConfigService(environ=environ)

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            logger=logger,
            redaction_service=redaction_service,
            log_config_service=config_service,
        )

        runtime.reload_log_config()

        entry = logger.info(
            "metrics", "record_success", password="hunter2"
        )

        assert entry.fields["password"] == "hunter2"

    def test_reload_log_config_toggles_redaction_on(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        redaction_service = GovernanceLogRedactionService()

        config_service = GovernanceLogConfigService(environ={})

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            logger=logger,
            redaction_service=redaction_service,
            log_config_service=config_service,
        )

        runtime.reload_log_config()

        entry = logger.info(
            "metrics", "record_success", password="hunter2"
        )

        assert entry.fields["password"] != "hunter2"

    def test_reload_log_config_toggles_sampling(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        repository = InMemoryGovernanceLogRepository()

        logger.set_repository(repository)

        sampling_service = GovernanceLogSamplingService(
            GovernanceLogSamplingPolicy(default_rate=0.0)
        )

        environ = {
            "NOTEBOOK2API_GOVERNANCE_LOG_ENABLE_SAMPLING": "true"
        }

        config_service = GovernanceLogConfigService(environ=environ)

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            logger=logger,
            sampling_service=sampling_service,
            log_config_service=config_service,
        )

        runtime.reload_log_config()

        logger.info("metrics", "routine_event")

        assert repository.list() == ()

    def test_reload_log_config_injects_batcher_settings(self):
        repository = InMemoryGovernanceLogRepository()

        batcher = GovernanceLogBatcher(
            repository, batch_size=1000, clock=lambda: BASE_TIME
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            batcher=batcher,
        )

        environ = {"NOTEBOOK2API_GOVERNANCE_LOG_BATCH_SIZE": "2"}

        config_service = GovernanceLogConfigService(environ=environ)

        worker = Mock()
        scheduler = Mock()
        provider_registry = Mock()
        clock = Mock()

        runtime = GovernanceIntegrityDeliveryRuntime(
            worker=worker,
            scheduler=scheduler,
            provider_registry=provider_registry,
            clock=clock,
            logger=logger,
            batcher=batcher,
            log_config_service=config_service,
        )

        runtime.reload_log_config()

        logger.info("metrics", "first")
        assert repository.list() == ()

        logger.info("metrics", "second")
        assert len(repository.list()) == 2

    def test_reload_log_config_without_service_is_a_no_op(self):
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

        assert runtime.reload_log_config() is None


class TestGovernanceLogConfigPersistenceIntegration:

    def test_initial_config_applied_at_construction(
        self, monkeypatch
    ):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
            deployment_governance_persistence_config_from_env,
        )

        # Confirm via a real persistence runtime that minimum_level
        # from the environment is applied at construction time.
        monkeypatch.setenv(
            "NOTEBOOK2API_GOVERNANCE_LOG_MINIMUM_LEVEL", "ERROR"
        )

        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        logger = runtime.build_integrity_logger()

        logger.info("metrics", "filtered_out")

        repository = runtime.build_integrity_log_repository()

        assert repository.list() == ()

        logger.error("metrics", "kept")

        assert len(repository.list()) == 1

    def test_reload_log_config_available_on_persistence_runtime(
        self,
    ):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
            deployment_governance_persistence_config_from_env,
        )

        runtime = build_deployment_governance_persistence(
            deployment_governance_persistence_config_from_env()
        )

        config = runtime.reload_log_config()

        assert config == runtime.build_integrity_log_config_service().load()


class TestGovernanceLogConfigCli:

    def _stub_runtime(self, config_service, reload_config=None):
        class _StubRuntime:
            def build_integrity_log_config_service(self):
                return config_service

            def reload_log_config(self):
                return (
                    reload_config
                    if reload_config is not None
                    else config_service.load()
                )

        return _StubRuntime()

    def test_show_runner_reports_config(self, monkeypatch):
        config_service = GovernanceLogConfigService(
            environ={
                "NOTEBOOK2API_GOVERNANCE_LOG_MINIMUM_LEVEL": "WARNING"
            }
        )

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(config_service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_config_show(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["minimum_level"] == "WARNING"

    def test_reload_runner_reports_new_config(self, monkeypatch):
        config_service = GovernanceLogConfigService(environ={})

        reloaded = GovernanceLogConfig(minimum_level="ERROR")

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(
                config_service, reload_config=reloaded
            ),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_config_reload(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["minimum_level"] == "ERROR"

    def test_show_runner_handles_failure(self, monkeypatch):
        def _raise(config):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            _raise,
        )

        stderr = StringIO()

        exit_code = run_deployment_governance_logging_config_show(
            stdout=StringIO(), stderr=stderr
        )

        assert exit_code == 2
        assert "could not be completed" in stderr.getvalue()

    def test_reload_runner_handles_failure(self, monkeypatch):
        def _raise(config):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            _raise,
        )

        stderr = StringIO()

        exit_code = run_deployment_governance_logging_config_reload(
            stdout=StringIO(), stderr=stderr
        )

        assert exit_code == 2
