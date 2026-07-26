import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.governance.deployment_dependency_graph import (
    CycleDetectedError,
    DeploymentDependencyGraph,
    DependencyEdge,
    UnknownDependencyError,
    UnknownNodeError,
    get_deployment_dependency_graph,
    router as deployment_dependency_graph_router,
)
from backend.governance.deployment_pipeline import (
    DeploymentPipelineEngine,
    PipelineStage,
    router as deployment_pipeline_router,
)
from backend.governance.deployment_stage_orchestrator import (
    DeploymentStageOrchestrator,
    OutOfSequenceError,
    router as deployment_stage_router,
)
from backend.governance.deployment_workflow import (
    DeploymentWorkflowEngine,
    router as deployment_workflow_router,
)


@pytest.fixture
def graph() -> DeploymentDependencyGraph:
    return DeploymentDependencyGraph()


def test_add_dependency_creates_edge_and_nodes(graph: DeploymentDependencyGraph):
    edge = graph.add_dependency("deploy", "build")

    assert isinstance(edge, DependencyEdge)
    assert edge.stage == "deploy"
    assert edge.depends_on == "build"
    assert {node.name for node in graph.nodes()} == {"deploy", "build"}


def test_add_dependency_requires_both_fields(graph: DeploymentDependencyGraph):
    with pytest.raises(ValueError):
        graph.add_dependency("", "build")


def test_add_dependency_rejects_self_dependency(graph: DeploymentDependencyGraph):
    with pytest.raises(ValueError):
        graph.add_dependency("build", "build")


def test_remove_dependency_deletes_edge(graph: DeploymentDependencyGraph):
    graph.add_dependency("deploy", "build")

    graph.remove_dependency("deploy", "build")

    assert graph.dependencies_of("deploy") == ()


def test_remove_dependency_unknown_raises(graph: DeploymentDependencyGraph):
    with pytest.raises(UnknownDependencyError):
        graph.remove_dependency("deploy", "build")


def test_dependencies_of_returns_direct_dependencies(graph: DeploymentDependencyGraph):
    graph.add_dependency("deploy", "build")
    graph.add_dependency("deploy", "test")

    assert graph.dependencies_of("deploy") == ("build", "test")


def test_dependencies_of_unknown_node_raises(graph: DeploymentDependencyGraph):
    with pytest.raises(UnknownNodeError):
        graph.dependencies_of("does-not-exist")


def test_dependents_of_returns_direct_dependents(graph: DeploymentDependencyGraph):
    graph.add_dependency("deploy", "build")
    graph.add_dependency("verify", "build")

    assert graph.dependents_of("build") == ("deploy", "verify")


def test_validate_returns_valid_with_order_for_acyclic_graph(graph: DeploymentDependencyGraph):
    graph.add_dependency("deploy", "build")
    graph.add_dependency("verify", "deploy")

    result = graph.validate()

    assert result["valid"] is True
    assert result["order"] == ("build", "deploy", "verify")
    assert result["cycles"] == ()


def test_validate_detects_cycle(graph: DeploymentDependencyGraph):
    graph.add_dependency("a", "b")
    graph.add_dependency("b", "c")
    graph.add_dependency("c", "a")

    result = graph.validate()

    assert result["valid"] is False
    assert result["cycles"]


def test_execution_order_returns_topological_order(graph: DeploymentDependencyGraph):
    graph.add_dependency("deploy", "build")
    graph.add_dependency("test", "build")

    order = graph.execution_order()

    assert order.index("build") < order.index("deploy")
    assert order.index("build") < order.index("test")


def test_execution_order_raises_on_cycle(graph: DeploymentDependencyGraph):
    graph.add_dependency("a", "b")
    graph.add_dependency("b", "a")

    with pytest.raises(CycleDetectedError):
        graph.execution_order()


def test_execution_order_breaks_ties_deterministically(graph: DeploymentDependencyGraph):
    graph.add_dependency("z", "root")
    graph.add_dependency("a", "root")

    order = graph.execution_order()

    assert order == ("root", "a", "z")


def _pipeline_with_execution(names):
    pipeline_engine = DeploymentPipelineEngine()
    pipeline_engine.register(
        "svc", [PipelineStage(name=name, action=name) for name in names]
    )
    wf_engine = DeploymentWorkflowEngine(pipeline_engine=pipeline_engine)
    execution = wf_engine.start("svc")
    return wf_engine, execution.execution_id


def test_orchestrator_respects_dependency_graph_order():
    wf_engine, execution_id = _pipeline_with_execution(["deploy", "build"])
    dep_graph = DeploymentDependencyGraph()
    dep_graph.add_dependency("deploy", "build")
    orchestrator = DeploymentStageOrchestrator(
        workflow_engine=wf_engine, dependency_graph=dep_graph
    )

    assert orchestrator.next_stage(execution_id) == "build"

    with pytest.raises(OutOfSequenceError):
        orchestrator.execute_stage(execution_id, "deploy")

    build_result = orchestrator.execute_stage(execution_id, "build")
    deploy_result = orchestrator.execute_stage(execution_id, "deploy")

    assert build_result.status == "SUCCEEDED"
    assert deploy_result.status == "SUCCEEDED"


def test_orchestrator_falls_back_to_pipeline_order_when_not_in_graph():
    wf_engine, execution_id = _pipeline_with_execution(["build", "deploy"])
    dep_graph = DeploymentDependencyGraph()
    orchestrator = DeploymentStageOrchestrator(
        workflow_engine=wf_engine, dependency_graph=dep_graph
    )

    assert orchestrator.next_stage(execution_id) == "build"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(deployment_pipeline_router)
    app.include_router(deployment_workflow_router)
    app.include_router(deployment_stage_router)
    app.include_router(deployment_dependency_graph_router)
    return TestClient(app)


def test_api_add_dependency(client: TestClient):
    response = client.post(
        "/governance/dependencies", json={"stage": "deploy-api-1", "depends_on": "build-api-1"}
    )

    assert response.status_code == 200
    assert response.json() == {"stage": "deploy-api-1", "depends_on": "build-api-1"}


def test_api_add_dependency_requires_fields(client: TestClient):
    response = client.post("/governance/dependencies", json={"stage": "deploy-api-2"})

    assert response.status_code == 422


def test_api_add_dependency_self_dependency_returns_422(client: TestClient):
    response = client.post(
        "/governance/dependencies", json={"stage": "build-api-3", "depends_on": "build-api-3"}
    )

    assert response.status_code == 422


def test_api_remove_dependency(client: TestClient):
    client.post(
        "/governance/dependencies", json={"stage": "deploy-api-4", "depends_on": "build-api-4"}
    )

    response = client.delete(
        "/governance/dependencies",
        params={"stage": "deploy-api-4", "depends_on": "build-api-4"},
    )

    assert response.status_code == 200
    assert response.json()["removed"] is True


def test_api_remove_dependency_unknown_returns_404(client: TestClient):
    response = client.delete(
        "/governance/dependencies",
        params={"stage": "deploy-api-5", "depends_on": "build-api-5"},
    )

    assert response.status_code == 404


def test_api_get_order(client: TestClient):
    client.post(
        "/governance/dependencies", json={"stage": "deploy-api-6", "depends_on": "build-api-6"}
    )

    response = client.get("/governance/dependencies/order")

    assert response.status_code == 200
    order = response.json()["order"]
    assert order.index("build-api-6") < order.index("deploy-api-6")


def test_api_get_order_cycle_returns_409(client: TestClient):
    client.post(
        "/governance/dependencies", json={"stage": "cycle-api-a", "depends_on": "cycle-api-b"}
    )
    client.post(
        "/governance/dependencies", json={"stage": "cycle-api-b", "depends_on": "cycle-api-a"}
    )

    try:
        response = client.get("/governance/dependencies/order")
        assert response.status_code == 409
    finally:
        get_deployment_dependency_graph().remove_dependency("cycle-api-a", "cycle-api-b")
        get_deployment_dependency_graph().remove_dependency("cycle-api-b", "cycle-api-a")
