import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_pipeline import (
    DeploymentPipelineEngine,
    PipelineAlreadyExistsError,
    PipelineStage,
    router as deployment_pipeline_router,
)
from backend.governance.deployment_templates import (
    DeploymentTemplateRegistry,
    PipelineTemplate,
    TemplateParameter,
    TemplateParameterError,
    UnknownTemplateError,
    router as deployment_templates_router,
)


def _stages():
    return [PipelineStage(name="build", action="build_${env}")]


@pytest.fixture
def registry() -> DeploymentTemplateRegistry:
    return DeploymentTemplateRegistry()


def test_register_creates_template(registry: DeploymentTemplateRegistry):
    template = registry.register("web-service", _stages())

    assert isinstance(template, PipelineTemplate)
    assert template.version == "1.0.0"
    assert len(template.stages) == 1


def test_register_requires_name(registry: DeploymentTemplateRegistry):
    with pytest.raises(ValueError):
        registry.register("", _stages())


def test_register_requires_stages(registry: DeploymentTemplateRegistry):
    with pytest.raises(ValueError):
        registry.register("web-service", [])


def test_register_rejects_duplicate_stage_names(registry: DeploymentTemplateRegistry):
    stages = [PipelineStage(name="build", action="a"), PipelineStage(name="build", action="b")]

    with pytest.raises(ValueError):
        registry.register("web-service", stages)


def test_register_rejects_invalid_version(registry: DeploymentTemplateRegistry):
    with pytest.raises(ValueError):
        registry.register("web-service", _stages(), version="bad")


def test_parameter_requires_name():
    with pytest.raises(ValueError):
        TemplateParameter(name="")


def test_parameter_required_and_default_conflict_raises():
    with pytest.raises(ValueError):
        TemplateParameter(name="env", required=True, default="prod")


def test_register_multiple_versions_and_version_lookup(registry: DeploymentTemplateRegistry):
    registry.register("web-service", _stages(), version="1.0.0")
    registry.register("web-service", _stages(), version="2.0.0")

    assert registry.get("web-service").version == "2.0.0"
    assert registry.get("web-service", "1.0.0").version == "1.0.0"


def test_register_with_extends_inherits_stages_and_parameters(
    registry: DeploymentTemplateRegistry,
):
    registry.register(
        "base",
        [PipelineStage(name="build", action="build")],
        parameters=[TemplateParameter(name="env", required=True)],
    )

    child = registry.register(
        "web-service",
        [PipelineStage(name="deploy", action="deploy")],
        extends="base",
        parameters=[TemplateParameter(name="region", default="us-east")],
    )

    stage_names = {stage.name for stage in child.stages}
    parameter_names = {parameter.name for parameter in child.parameters}
    assert stage_names == {"build", "deploy"}
    assert parameter_names == {"env", "region"}


def test_register_extends_unknown_parent_raises(registry: DeploymentTemplateRegistry):
    with pytest.raises(UnknownTemplateError):
        registry.register("web-service", _stages(), extends="does-not-exist")


def test_remove_specific_version(registry: DeploymentTemplateRegistry):
    registry.register("web-service", _stages(), version="1.0.0")
    registry.register("web-service", _stages(), version="2.0.0")

    registry.remove("web-service", "2.0.0")

    assert registry.get("web-service").version == "1.0.0"


def test_remove_entire_template(registry: DeploymentTemplateRegistry):
    registry.register("web-service", _stages())

    registry.remove("web-service")

    with pytest.raises(UnknownTemplateError):
        registry.get("web-service")


def test_remove_unknown_raises(registry: DeploymentTemplateRegistry):
    with pytest.raises(UnknownTemplateError):
        registry.remove("does-not-exist")


def test_get_unknown_raises(registry: DeploymentTemplateRegistry):
    with pytest.raises(UnknownTemplateError):
        registry.get("does-not-exist")


def test_list_returns_latest_of_each_template(registry: DeploymentTemplateRegistry):
    registry.register("a", _stages(), version="1.0.0")
    registry.register("a", _stages(), version="2.0.0")
    registry.register("b", _stages())

    versions = {template.name: template.version for template in registry.list()}

    assert versions == {"a": "2.0.0", "b": "1.0.0"}


def test_instantiate_substitutes_parameter_values(registry: DeploymentTemplateRegistry):
    registry.register(
        "web-service", _stages(), parameters=[TemplateParameter(name="env", required=True)]
    )
    pipeline_engine = DeploymentPipelineEngine()

    pipeline = registry.instantiate(
        "web-service",
        parameters={"env": "staging"},
        pipeline_engine=pipeline_engine,
    )

    assert pipeline.stages[0].action == "build_staging"
    assert pipeline.metadata["template"] == "web-service"


def test_instantiate_uses_default_when_omitted(registry: DeploymentTemplateRegistry):
    registry.register(
        "web-service", _stages(), parameters=[TemplateParameter(name="env", default="prod")]
    )
    pipeline_engine = DeploymentPipelineEngine()

    pipeline = registry.instantiate("web-service", pipeline_engine=pipeline_engine)

    assert pipeline.stages[0].action == "build_prod"


def test_instantiate_missing_required_parameter_raises(registry: DeploymentTemplateRegistry):
    registry.register(
        "web-service", _stages(), parameters=[TemplateParameter(name="env", required=True)]
    )
    pipeline_engine = DeploymentPipelineEngine()

    with pytest.raises(TemplateParameterError):
        registry.instantiate("web-service", pipeline_engine=pipeline_engine)


def test_instantiate_unknown_parameter_raises(registry: DeploymentTemplateRegistry):
    registry.register("web-service", _stages())
    pipeline_engine = DeploymentPipelineEngine()

    with pytest.raises(TemplateParameterError):
        registry.instantiate(
            "web-service", parameters={"bogus": "1"}, pipeline_engine=pipeline_engine
        )


def test_instantiate_unknown_template_raises(registry: DeploymentTemplateRegistry):
    pipeline_engine = DeploymentPipelineEngine()

    with pytest.raises(UnknownTemplateError):
        registry.instantiate("does-not-exist", pipeline_engine=pipeline_engine)


def test_instantiate_requires_pipeline_engine(registry: DeploymentTemplateRegistry):
    registry.register("web-service", _stages())

    with pytest.raises(ValueError):
        registry.instantiate("web-service")


def test_instantiate_duplicate_pipeline_name_raises(registry: DeploymentTemplateRegistry):
    registry.register(
        "web-service", _stages(), parameters=[TemplateParameter(name="env", default="prod")]
    )
    pipeline_engine = DeploymentPipelineEngine()
    registry.instantiate("web-service", pipeline_engine=pipeline_engine)

    with pytest.raises(PipelineAlreadyExistsError):
        registry.instantiate("web-service", pipeline_engine=pipeline_engine)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_pipeline_router)
    app.include_router(deployment_templates_router)
    return TestClient(app)


def test_api_register_and_list(client: TestClient):
    create_response = client.post(
        "/governance/templates",
        json={
            "name": "web-service-api-1",
            "stages": [{"name": "build", "action": "build_${env}"}],
            "parameters": [{"name": "env", "default": "prod"}],
        },
    )
    list_response = client.get("/governance/templates")

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert any(t["name"] == "web-service-api-1" for t in list_response.json())


def test_api_register_requires_fields(client: TestClient):
    response = client.post("/governance/templates", json={})

    assert response.status_code == 422


def test_api_register_extends_unknown_returns_404(client: TestClient):
    response = client.post(
        "/governance/templates",
        json={
            "name": "web-service-api-2",
            "stages": [{"name": "build", "action": "build"}],
            "extends": "does-not-exist",
        },
    )

    assert response.status_code == 404


def test_api_instantiate(client: TestClient):
    client.post(
        "/governance/templates",
        json={
            "name": "web-service-api-3",
            "stages": [{"name": "build", "action": "build_${env}"}],
            "parameters": [{"name": "env", "required": True}],
        },
    )

    response = client.post(
        "/governance/templates/web-service-api-3/instantiate",
        json={"parameters": {"env": "staging"}},
    )

    assert response.status_code == 200
    assert response.json()["stages"][0]["action"] == "build_staging"


def test_api_instantiate_missing_required_param_returns_422(client: TestClient):
    client.post(
        "/governance/templates",
        json={
            "name": "web-service-api-4",
            "stages": [{"name": "build", "action": "build"}],
            "parameters": [{"name": "env", "required": True}],
        },
    )

    response = client.post("/governance/templates/web-service-api-4/instantiate", json={})

    assert response.status_code == 422


def test_api_instantiate_unknown_template_returns_404(client: TestClient):
    response = client.post(
        "/governance/templates/does-not-exist/instantiate", json={}
    )

    assert response.status_code == 404
