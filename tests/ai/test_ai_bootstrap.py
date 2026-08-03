import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.bootstrap import (
    REQUIRED_SERVICES,
    SUBSYSTEM_NAME,
    AIBootstrapError,
    AINotInitializedError,
    AIPlatformBootstrap,
    UnknownServiceError,
    bootstrap_ai_subsystem,
    get_ai_bootstrap,
)
from backend.ai.model_registry import ModelMetadata
from backend.ai.prompt_templates import TemplateVariable


def test_register_services_wires_every_required_service():
    bootstrap = AIPlatformBootstrap()

    services = bootstrap.register_services()

    assert set(services) == set(REQUIRED_SERVICES)
    assert all(value is not None for value in services.values())


def test_registered_services_reflects_last_register_call():
    bootstrap = AIPlatformBootstrap()

    assert bootstrap.registered_services() == {}

    bootstrap.register_services()

    assert set(bootstrap.registered_services()) == set(REQUIRED_SERVICES)


def test_discover_returns_named_service():
    bootstrap = AIPlatformBootstrap()
    bootstrap.register_services()

    routing_engine = bootstrap.discover("routing_engine")

    assert routing_engine is bootstrap.registered_services()["routing_engine"]


def test_discover_unknown_service_raises():
    bootstrap = AIPlatformBootstrap()
    bootstrap.register_services()

    with pytest.raises(UnknownServiceError):
        bootstrap.discover("does-not-exist")


def test_wire_components_restores_deployed_models():
    bootstrap = AIPlatformBootstrap()
    services = bootstrap.register_services()
    model_name = f"e2e-model-{uuid.uuid4().hex}"
    services["model_registry"].register(
        model_name, "1.0.0", ModelMetadata(entry_point="models.e2e:Model")
    )
    deployment = services["deployment_manager"].deploy(
        model_name, "1.0.0", registry=services["model_registry"]
    )

    restored = bootstrap.wire_components()

    assert deployment.deployment_id in restored
    assert services["model_loader"].is_loaded(model_name) is True


def test_wire_components_skips_deployments_with_invalid_manifest():
    bootstrap = AIPlatformBootstrap()
    services = bootstrap.register_services()
    model_name = f"broken-model-{uuid.uuid4().hex}"
    services["model_registry"].register(model_name, "1.0.0")  # no entry_point
    deployment = services["deployment_manager"].deploy(
        model_name, "1.0.0", registry=services["model_registry"]
    )

    restored = bootstrap.wire_components()

    assert deployment.deployment_id not in restored
    assert services["model_loader"].is_loaded(model_name) is False


def test_wire_components_is_idempotent():
    bootstrap = AIPlatformBootstrap()
    bootstrap.register_services()

    first = bootstrap.wire_components()
    second = bootstrap.wire_components()

    assert first == second


def test_initialize_returns_valid_result():
    bootstrap = AIPlatformBootstrap()

    result = bootstrap.initialize()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)
    assert result.missing_services == ()
    assert bootstrap.is_initialized is True


def test_initialize_raises_when_a_required_service_is_missing(monkeypatch):
    bootstrap = AIPlatformBootstrap()
    incomplete = {name: object() for name in REQUIRED_SERVICES if name != "dashboard_api"}
    monkeypatch.setattr(bootstrap, "register_services", lambda: incomplete)
    monkeypatch.setattr(bootstrap, "wire_components", lambda: ())
    monkeypatch.setattr(bootstrap, "_count_routing_rules", lambda: 0)

    with pytest.raises(AIBootstrapError) as exc_info:
        bootstrap.initialize()

    assert exc_info.value.result.missing_services == ("dashboard_api",)
    assert exc_info.value.result.valid is False
    assert bootstrap.is_initialized is False


def test_health_check_before_initialize_raises():
    bootstrap = AIPlatformBootstrap()

    with pytest.raises(AINotInitializedError):
        bootstrap.health_check()


def test_health_check_delegates_to_the_dashboard():
    bootstrap = AIPlatformBootstrap()
    bootstrap.initialize()

    report = bootstrap.health_check()

    assert report["status"] == "ok"
    assert "models" in report
    assert "deployments" in report


def test_shutdown_before_initialize_raises():
    bootstrap = AIPlatformBootstrap()

    with pytest.raises(AINotInitializedError):
        bootstrap.shutdown()


def test_shutdown_resets_state():
    bootstrap = AIPlatformBootstrap()
    bootstrap.initialize()

    bootstrap.shutdown()

    assert bootstrap.is_initialized is False


def test_bootstrap_ai_subsystem_is_valid():
    result = bootstrap_ai_subsystem()

    assert result.valid is True
    assert set(result.registered_services) == set(REQUIRED_SERVICES)


def test_bootstrap_ai_subsystem_is_idempotent():
    first = bootstrap_ai_subsystem()
    second = bootstrap_ai_subsystem()

    assert first.valid is True
    assert second.valid is True


def test_get_ai_bootstrap_returns_singleton():
    assert get_ai_bootstrap() is get_ai_bootstrap()


def test_subsystem_name_is_stable():
    assert SUBSYSTEM_NAME == "ai_model_management_and_inference_platform"


def test_end_to_end_inference_workflow():
    result = bootstrap_ai_subsystem()
    assert result.valid is True

    services = get_ai_bootstrap().registered_services()
    registry = services["model_registry"]
    loader = services["model_loader"]
    inference_engine = services["inference_engine"]
    versions = services["version_manager"]
    templates = services["template_manager"]
    routing_engine = services["routing_engine"]
    benchmark_service = services["benchmark_service"]
    deployment_manager = services["deployment_manager"]
    analytics = services["analytics_service"]
    dashboard_api = services["dashboard_api"]
    export_service = services["export_service"]

    suffix = uuid.uuid4().hex
    model_name = f"e2e-model-{suffix}"

    versions.create(
        model_name, "1.0.0", ModelMetadata(entry_point="models.e2e:Model", capabilities=("chat",)),
        registry=registry,
    )
    loader.load(model_name, registry=registry)
    templates.create(f"greeting-{suffix}", "Hello {name}", [TemplateVariable(name="name")])
    routing_engine.register_route(f"route-{suffix}", "priority", candidates=[model_name])
    decision = routing_engine.select(f"route-{suffix}", registry=registry)
    assert decision.selected_model == model_name

    result_infer = inference_engine.infer(model_name, {"prompt": "hi"}, loader=loader, analytics=analytics)
    assert result_infer.state.value == "succeeded"

    benchmark_result = benchmark_service.run("standard-suite", model_name, registry=registry)
    assert benchmark_result.model_name == model_name

    deployment = deployment_manager.deploy(model_name, registry=registry, versions=versions)
    assert deployment.model_name == model_name

    overview = dashboard_api.overview()
    assert overview["models"]["total_models"] >= 1

    manifest = export_service.export_all()
    assert manifest.sections == ("models", "deployments", "benchmarks", "analytics")


@pytest.fixture
def client() -> TestClient:
    from backend.ai.model_registry import router as model_registry_router
    from backend.ai.model_loader import router as model_loader_router
    from backend.ai.inference_engine import router as inference_router
    from backend.ai.model_versioning import router as versioning_router
    from backend.ai.prompt_templates import router as templates_router
    from backend.ai.batch_inference import router as batch_router
    from backend.ai.model_routing import router as routing_router
    from backend.ai.model_benchmark import router as benchmark_router
    from backend.ai.model_deployment import router as deployment_router
    from backend.ai.inference_analytics import router as analytics_router
    from backend.ai.dashboard import router as dashboard_router, export_router

    app = FastAPI()
    for router in (
        model_loader_router,
        inference_router,
        versioning_router,
        templates_router,
        batch_router,
        routing_router,
        benchmark_router,
        deployment_router,
        analytics_router,
        dashboard_router,
        export_router,
        model_registry_router,
    ):
        app.include_router(router)
    return TestClient(app)


def test_route_registration_covers_every_service_area(client: TestClient):
    bootstrap_ai_subsystem()

    assert client.get("/ai/dashboard").status_code == 200
    assert client.get("/ai/models").status_code == 200
    assert client.get("/ai/models/loaded").status_code == 200
    assert client.get("/ai/routing").status_code == 200
    assert client.get("/ai/benchmarks").status_code == 200
    assert client.get("/ai/analytics").status_code == 200
    assert client.get("/ai/prompts").status_code == 200
    assert client.get("/ai/export/all").status_code == 200
