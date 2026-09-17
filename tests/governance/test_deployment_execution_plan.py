import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_dependency_graph import (
    DeploymentDependencyGraph,
    router as deployment_dependency_graph_router,
)
from backend.governance.deployment_execution_plan import (
    DeploymentExecutionPlanBuilder,
    ExecutionPlan,
    ExecutionStep,
    PlanValidationError,
    UnknownPlanError,
    router as deployment_execution_plan_router,
)
from backend.governance.deployment_pipeline import (
    DeploymentPipelineEngine,
    PipelineStage,
    UnknownPipelineError,
    router as deployment_pipeline_router,
)


def _pipeline_engine(name, stage_names):
    engine = DeploymentPipelineEngine()
    engine.register(name, [PipelineStage(name=n, action=n) for n in stage_names])
    return engine


@pytest.fixture
def builder() -> DeploymentExecutionPlanBuilder:
    return DeploymentExecutionPlanBuilder()


def test_build_without_graph_produces_one_stage_per_group(builder: DeploymentExecutionPlanBuilder):
    engine = _pipeline_engine("svc-a", ["build", "test", "deploy"])

    plan = builder.build("svc-a", pipeline_engine=engine)

    assert isinstance(plan, ExecutionPlan)
    assert [step.group for step in plan.steps] == [0, 1, 2]
    assert [step.stage for step in plan.steps] == ["build", "test", "deploy"]


def test_build_unknown_pipeline_raises(builder: DeploymentExecutionPlanBuilder):
    engine = DeploymentPipelineEngine()

    with pytest.raises(UnknownPipelineError):
        builder.build("does-not-exist", pipeline_engine=engine)


def test_build_requires_pipeline_engine(builder: DeploymentExecutionPlanBuilder):
    with pytest.raises(ValueError):
        builder.build("svc-a")


def test_build_groups_independent_stages_in_parallel(builder: DeploymentExecutionPlanBuilder):
    engine = _pipeline_engine(
        "svc-a", ["build", "unit_test", "integration_test", "deploy"]
    )
    graph = DeploymentDependencyGraph()
    graph.add_dependency("unit_test", "build")
    graph.add_dependency("integration_test", "build")
    graph.add_dependency("deploy", "unit_test")
    graph.add_dependency("deploy", "integration_test")

    plan = builder.build("svc-a", pipeline_engine=engine, dependency_graph=graph)

    groups = {step.stage: step.group for step in plan.steps}
    assert groups["build"] == 0
    assert groups["unit_test"] == groups["integration_test"] == 1
    assert groups["deploy"] == 2


def test_build_respects_dependency_ordering(builder: DeploymentExecutionPlanBuilder):
    engine = _pipeline_engine("svc-a", ["build", "deploy"])
    graph = DeploymentDependencyGraph()
    graph.add_dependency("deploy", "build")

    plan = builder.build("svc-a", pipeline_engine=engine, dependency_graph=graph)

    groups = {step.stage: step.group for step in plan.steps}
    assert groups["build"] < groups["deploy"]
    deploy_step = next(step for step in plan.steps if step.stage == "deploy")
    assert deploy_step.depends_on == ("build",)


def test_build_raises_on_cyclic_dependencies(builder: DeploymentExecutionPlanBuilder):
    engine = _pipeline_engine("svc-a", ["a", "b"])
    graph = DeploymentDependencyGraph()
    graph.add_dependency("a", "b")
    graph.add_dependency("b", "a")

    with pytest.raises(PlanValidationError):
        builder.build("svc-a", pipeline_engine=engine, dependency_graph=graph)


def test_validate_detects_duplicate_stage(builder: DeploymentExecutionPlanBuilder):
    plan = ExecutionPlan(
        plan_id="p1",
        pipeline="svc-a",
        version="1.0.0",
        steps=(
            ExecutionStep(stage="build", group=0),
            ExecutionStep(stage="build", group=1),
        ),
    )

    result = builder.validate(plan)

    assert result["valid"] is False
    assert any("more than once" in error for error in result["errors"])


def test_validate_detects_dependency_ordering_violation(builder: DeploymentExecutionPlanBuilder):
    plan = ExecutionPlan(
        plan_id="p2",
        pipeline="svc-a",
        version="1.0.0",
        steps=(
            ExecutionStep(stage="deploy", group=0, depends_on=("build",)),
            ExecutionStep(stage="build", group=1),
        ),
    )

    result = builder.validate(plan)

    assert result["valid"] is False
    assert any("depends on" in error for error in result["errors"])


def test_validate_accepts_well_formed_plan(builder: DeploymentExecutionPlanBuilder):
    plan = ExecutionPlan(
        plan_id="p3",
        pipeline="svc-a",
        version="1.0.0",
        steps=(
            ExecutionStep(stage="build", group=0),
            ExecutionStep(stage="deploy", group=1, depends_on=("build",)),
        ),
    )

    result = builder.validate(plan)

    assert result["valid"] is True
    assert result["errors"] == ()


def test_optimize_compacts_group_gaps(builder: DeploymentExecutionPlanBuilder):
    gapped = ExecutionPlan(
        plan_id="gapped",
        pipeline="svc-a",
        version="1.0.0",
        steps=(
            ExecutionStep(stage="build", group=0),
            ExecutionStep(stage="deploy", group=5),
        ),
    )
    builder._plans[gapped.plan_id] = gapped

    optimized = builder.optimize("gapped")

    assert [step.group for step in optimized.steps] == [0, 1]
    assert builder.preview("gapped") == optimized


def test_optimize_unknown_plan_raises(builder: DeploymentExecutionPlanBuilder):
    with pytest.raises(UnknownPlanError):
        builder.optimize("does-not-exist")


def test_preview_returns_built_plan(builder: DeploymentExecutionPlanBuilder):
    engine = _pipeline_engine("svc-a", ["build"])
    plan = builder.build("svc-a", pipeline_engine=engine)

    assert builder.preview(plan.plan_id) == plan


def test_preview_unknown_plan_raises(builder: DeploymentExecutionPlanBuilder):
    with pytest.raises(UnknownPlanError):
        builder.preview("does-not-exist")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_pipeline_router)
    app.include_router(deployment_dependency_graph_router)
    app.include_router(deployment_execution_plan_router)
    return TestClient(app)


def test_api_create_and_get_plan(client: TestClient):
    client.post(
        "/governance/pipelines",
        json={
            "name": "plan-svc-api-1",
            "stages": [{"name": "build", "action": "build"}, {"name": "deploy", "action": "deploy"}],
        },
    )

    create_response = client.post(
        "/governance/execution-plans", json={"pipeline": "plan-svc-api-1"}
    )
    plan_id = create_response.json()["plan_id"]
    get_response = client.get(f"/governance/execution-plans/{plan_id}")

    assert create_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.json()["pipeline"] == "plan-svc-api-1"


def test_api_create_requires_pipeline(client: TestClient):
    response = client.post("/governance/execution-plans", json={})

    assert response.status_code == 422


def test_api_create_unknown_pipeline_returns_404(client: TestClient):
    response = client.post(
        "/governance/execution-plans", json={"pipeline": "does-not-exist"}
    )

    assert response.status_code == 404


def test_api_create_cyclic_dependencies_returns_422(client: TestClient):
    client.post(
        "/governance/pipelines",
        json={
            "name": "plan-svc-api-2",
            "stages": [{"name": "cyc-a", "action": "a"}, {"name": "cyc-b", "action": "b"}],
        },
    )
    client.post("/governance/dependencies", json={"stage": "cyc-a", "depends_on": "cyc-b"})
    client.post("/governance/dependencies", json={"stage": "cyc-b", "depends_on": "cyc-a"})

    try:
        response = client.post(
            "/governance/execution-plans", json={"pipeline": "plan-svc-api-2"}
        )
        assert response.status_code == 422
    finally:
        from backend.governance.deployment_dependency_graph import (
            get_deployment_dependency_graph,
        )

        get_deployment_dependency_graph().remove_dependency("cyc-a", "cyc-b")
        get_deployment_dependency_graph().remove_dependency("cyc-b", "cyc-a")


def test_api_get_unknown_plan_returns_404(client: TestClient):
    response = client.get("/governance/execution-plans/does-not-exist")

    assert response.status_code == 404
