import pytest
from fastapi import FastAPI

from backend.observability.deployment_governance_metrics_bootstrap import (
    GovernanceIntegrityMetricsBootstrap,
    GovernanceIntegrityMetricsBootstrapHealth,
    build_integrity_metrics_bootstrap,
)
from backend.observability.deployment_governance_persistence import (
    build_deployment_governance_persistence,
    deployment_governance_persistence_config_from_env,
)


def _persistence_runtime():
    return build_deployment_governance_persistence(
        deployment_governance_persistence_config_from_env()
    )


class TestGovernanceIntegrityMetricsBootstrapDependencyValidation:

    def test_rejects_none_persistence_runtime(self):
        with pytest.raises(ValueError):
            GovernanceIntegrityMetricsBootstrap(None)


class TestGovernanceIntegrityMetricsBootstrapBuild:

    def test_health_before_build(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        )

        health = bootstrap.health()

        assert health.built is False
        assert health.initialized is False
        assert health.collector_running is False

    def test_successful_build_constructs_every_service(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        )

        bootstrap.build()

        assert bootstrap.config_service is not None
        assert bootstrap.metrics_service is not None
        assert bootstrap.alert_service is not None
        assert bootstrap.retention_service is not None
        assert bootstrap.metrics_collector is not None
        assert bootstrap.dashboard_service is not None
        assert bootstrap.api is not None

    def test_build_does_not_start_the_collector(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        )

        bootstrap.build()

        assert bootstrap.metrics_collector.is_running() is False

    def test_build_returns_self(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        )

        result = bootstrap.build()

        assert result is bootstrap

    def test_double_build_raises_error(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        )

        bootstrap.build()

        with pytest.raises(RuntimeError, match="already been built"):
            bootstrap.build()


class TestGovernanceIntegrityMetricsBootstrapInitialize:

    def test_initialize_without_build_raises_error(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        )

        with pytest.raises(RuntimeError, match="must be built"):
            bootstrap.initialize()

    def test_initialize_starts_the_collector(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.initialize()

        try:
            assert bootstrap.metrics_collector.is_running() is True

        finally:
            bootstrap.shutdown()

    def test_initialize_loads_durable_metrics(self):
        runtime = _persistence_runtime()

        runtime.build_integrity_metrics_service().record_success(
            50.0
        )

        bootstrap = GovernanceIntegrityMetricsBootstrap(runtime).build()

        bootstrap.initialize()

        try:
            assert (
                bootstrap.metrics_service.snapshot().total_dispatches
                == 1
            )

        finally:
            bootstrap.shutdown()

    def test_initialize_evaluates_alerts(self):
        runtime = _persistence_runtime()

        service = runtime.build_integrity_metrics_service()

        for _ in range(9):
            service.record_failure(10.0)

        service.record_success(10.0)

        bootstrap = GovernanceIntegrityMetricsBootstrap(runtime).build()

        bootstrap.initialize()

        try:
            assert bootstrap.health().active_alerts >= 1

        finally:
            bootstrap.shutdown()

    def test_double_initialize_raises_error(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.initialize()

        try:
            with pytest.raises(
                RuntimeError, match="already been initialized"
            ):
                bootstrap.initialize()

        finally:
            bootstrap.shutdown()

    def test_initialize_registers_middleware_when_app_given(self):
        app = FastAPI()

        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime(), app=app
        ).build()

        bootstrap.initialize()

        try:
            from backend.observability.deployment_governance_metrics_middleware import (
                GovernanceIntegrityMetricsMiddleware,
            )

            middleware_classes = [
                m.cls for m in app.user_middleware
            ]

            assert (
                GovernanceIntegrityMetricsMiddleware
                in middleware_classes
            )

        finally:
            bootstrap.shutdown()

    def test_initialize_without_app_registers_no_middleware(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.initialize()

        try:
            assert bootstrap._app is None

        finally:
            bootstrap.shutdown()

    def test_initialize_applies_current_config(self):
        runtime = _persistence_runtime()

        import os

        os.environ[
            "NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS"
        ] = "17"

        try:
            bootstrap = GovernanceIntegrityMetricsBootstrap(
                runtime
            ).build()

            bootstrap.initialize()

            try:
                assert (
                    bootstrap.metrics_collector._interval_seconds
                    == 17
                )

            finally:
                bootstrap.shutdown()

        finally:
            del os.environ[
                "NOTEBOOK2API_GOVERNANCE_METRICS_COLLECTION_INTERVAL_SECONDS"
            ]


class TestGovernanceIntegrityMetricsBootstrapShutdown:

    def test_shutdown_before_initialize_is_a_no_op(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.shutdown()

        assert bootstrap.health().initialized is False

    def test_shutdown_stops_the_collector(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.initialize()
        bootstrap.shutdown()

        assert bootstrap.metrics_collector.is_running() is False

    def test_shutdown_is_idempotent(self):
        bootstrap = GovernanceIntegrityMetricsBootstrap(
            _persistence_runtime()
        ).build()

        bootstrap.initialize()
        bootstrap.shutdown()
        bootstrap.shutdown()

        assert bootstrap.health().initialized is False

    def test_shutdown_flushes_metrics(self):
        runtime = _persistence_runtime()

        bootstrap = GovernanceIntegrityMetricsBootstrap(runtime).build()

        bootstrap.initialize()

        bootstrap.metrics_service.record_success(10.0)

        bootstrap.shutdown()

        repository = runtime.build_integrity_metrics_repository()

        assert repository.load() is not None


class TestGovernanceIntegrityMetricsBootstrapHealthModel:

    def test_health_to_dict(self):
        health = GovernanceIntegrityMetricsBootstrapHealth(
            built=True,
            initialized=True,
            collector_running=True,
            active_alerts=2,
        )

        assert health.to_dict() == {
            "built": True,
            "initialized": True,
            "collector_running": True,
            "active_alerts": 2,
        }


class TestBuildIntegrityMetricsBootstrap:

    def test_build_integrity_metrics_bootstrap_returns_built_instance(
        self,
    ):
        bootstrap = build_integrity_metrics_bootstrap(
            _persistence_runtime()
        )

        assert isinstance(bootstrap, GovernanceIntegrityMetricsBootstrap)
        assert bootstrap.health().built is True
        assert bootstrap.health().initialized is False

    def test_build_integrity_metrics_bootstrap_rejects_none(self):
        with pytest.raises(ValueError):
            build_integrity_metrics_bootstrap(None)
