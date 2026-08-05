import pytest

from backend.observability.bootstrap import ObservabilityBootstrap
from backend.observability.service_discovery import ServiceNode


class TestBootstrapInitialization:
    def test_initialize_starts_runtime(self):
        bootstrap = ObservabilityBootstrap()

        runtime = bootstrap.initialize()

        assert bootstrap.state == "running"
        assert runtime is not None

    def test_initialize_twice_raises(self):
        bootstrap = ObservabilityBootstrap()
        bootstrap.initialize()

        with pytest.raises(ValueError):
            bootstrap.initialize()

    def test_shutdown_without_running_raises(self):
        bootstrap = ObservabilityBootstrap()

        with pytest.raises(ValueError):
            bootstrap.shutdown()

    def test_shutdown_after_initialize(self):
        bootstrap = ObservabilityBootstrap()
        bootstrap.initialize()

        bootstrap.shutdown()

        assert bootstrap.state == "shutdown"


class TestDependencyWiring:
    def test_wire_components_requires_registration_first(self):
        bootstrap = ObservabilityBootstrap()

        with pytest.raises(ValueError):
            bootstrap.wire_components()

    def test_wire_components_registers_pipeline_health_check(self):
        bootstrap = ObservabilityBootstrap()
        bootstrap.register_services()

        runtime = bootstrap.wire_components()

        assert runtime.health_framework.is_registered("telemetry_pipeline")

    def test_wire_components_registers_discovered_topology(self):
        def scan_fn():
            return [ServiceNode(name="gateway", source="gateway", address="10.0.0.1")]

        bootstrap = ObservabilityBootstrap(discovery_scan_fn=scan_fn)
        bootstrap.register_services()

        runtime = bootstrap.wire_components()

        assert runtime.health_framework.is_registered("gateway")


class TestTelemetryFlow:
    def test_metrics_flow_from_registry_through_storage(self):
        bootstrap = ObservabilityBootstrap()
        runtime = bootstrap.initialize()

        runtime.analytics_service.record("request_rate", 100)

        assert runtime.storage_engine.values("request_rate") == [100]

    def test_traces_flow_into_export_service(self):
        bootstrap = ObservabilityBootstrap()
        runtime = bootstrap.initialize()

        context = runtime.tracing_engine.start_span("handle request", "http")
        runtime.tracing_engine.finish_span(context.span_id)

        export = runtime.export_service.export_traces(context.trace_id)

        assert export.export_type == "traces"


class TestDashboardAvailability:
    def test_dashboard_overview_reflects_wired_health_check(self):
        bootstrap = ObservabilityBootstrap()
        runtime = bootstrap.initialize()

        overview = runtime.dashboard.overview()

        assert overview["health"]["status"] == "healthy"


class TestEndToEndPipeline:
    def test_full_pipeline_metrics_alerts_and_dashboard(self):
        bootstrap = ObservabilityBootstrap()
        runtime = bootstrap.initialize()

        runtime.analytics_service.record("error_rate", 5)
        runtime.log_service.ingest("api", "error", "request failed")

        overview = runtime.dashboard.overview()

        assert overview["metrics"]["error_rate"]["latest"] == 5
        assert overview["health"]["status"] == "healthy"

        manifest = runtime.export_service.export_all(["error_rate"])
        export_types = {export.export_type for export in manifest.exports}
        assert "metrics" in export_types
        assert "logs" in export_types

        bootstrap.shutdown()
        assert bootstrap.state == "shutdown"
