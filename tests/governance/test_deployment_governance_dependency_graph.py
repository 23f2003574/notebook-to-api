from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_dependency_graph import (
    DependencyValidationResult,
    GovernanceComponent,
    GovernanceDependencyGraph,
)


# --- Model -------------------------------------------------------------


def test_component_rejects_empty_name():
    with pytest.raises(ValueError, match="name must not be empty"):
        GovernanceComponent(name="", dependencies=())


def test_component_to_dict():
    component = GovernanceComponent(
        name="delivery_runtime", dependencies=("provider_registry",)
    )

    assert component.to_dict() == {
        "name": "delivery_runtime",
        "dependencies": ["provider_registry"],
    }


def test_validation_result_to_dict():
    result = DependencyValidationResult(
        valid=False,
        startup_order=(),
        cycles=(("a", "b", "a"),),
        missing=("c",),
    )

    assert result.to_dict() == {
        "valid": False,
        "startup_order": [],
        "cycles": [["a", "b", "a"]],
        "missing": ["c"],
    }


# --- Registration --------------------------------------------------------


class TestGovernanceDependencyGraphRegistration:

    def test_register_and_components(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=())
        graph.register("b", dependencies=("a",))

        names = [c.name for c in graph.components()]

        assert names == ["a", "b"]

    def test_duplicate_registration_raises(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=())

        with pytest.raises(ValueError, match="already registered"):
            graph.register("a", dependencies=())

    def test_register_without_dependencies_defaults_to_empty(self):
        graph = GovernanceDependencyGraph()
        graph.register("a")

        assert graph.dependencies("a") == ()

    def test_remove_component(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=())
        graph.remove("a")

        assert graph.components() == ()

    def test_remove_missing_component_raises(self):
        graph = GovernanceDependencyGraph()

        with pytest.raises(KeyError):
            graph.remove("a")

    def test_dependency_does_not_need_to_be_registered_first(self):
        graph = GovernanceDependencyGraph()
        graph.register("b", dependencies=("a",))
        graph.register("a", dependencies=())

        assert graph.validate().valid is True


# --- Dependency lookup -----------------------------------------------


class TestGovernanceDependencyGraphLookup:

    def test_dependencies_returns_registered_dependencies(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=())
        graph.register("b", dependencies=("a",))

        assert graph.dependencies("b") == ("a",)

    def test_dependencies_of_unregistered_component_raises(self):
        graph = GovernanceDependencyGraph()

        with pytest.raises(KeyError):
            graph.dependencies("missing")

    def test_dependents_returns_components_that_depend_on_name(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=())
        graph.register("b", dependencies=("a",))
        graph.register("c", dependencies=("a",))

        assert graph.dependents("a") == ("b", "c")

    def test_dependents_of_leaf_component_is_empty(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=())
        graph.register("b", dependencies=("a",))

        assert graph.dependents("b") == ()

    def test_dependents_does_not_require_name_to_be_registered(self):
        graph = GovernanceDependencyGraph()
        graph.register("b", dependencies=("a",))

        assert graph.dependents("a") == ("b",)


# --- Startup ordering -------------------------------------------------


class TestGovernanceDependencyGraphStartupOrdering:

    def test_startup_order_respects_dependencies(self):
        graph = GovernanceDependencyGraph()
        graph.register("delivery_runtime", dependencies=("provider_registry",))
        graph.register("provider_registry", dependencies=())

        order = graph.startup_order()

        assert order.index("provider_registry") < order.index(
            "delivery_runtime"
        )

    def test_startup_order_is_deterministic_regardless_of_registration_order(
        self,
    ):
        graph_a = GovernanceDependencyGraph()
        graph_a.register("b", dependencies=("a",))
        graph_a.register("a", dependencies=())
        graph_a.register("c", dependencies=("a",))

        graph_b = GovernanceDependencyGraph()
        graph_b.register("a", dependencies=())
        graph_b.register("c", dependencies=("a",))
        graph_b.register("b", dependencies=("a",))

        assert graph_a.startup_order() == graph_b.startup_order()

    def test_startup_order_breaks_ties_alphabetically(self):
        graph = GovernanceDependencyGraph()
        graph.register("z", dependencies=())
        graph.register("a", dependencies=())
        graph.register("m", dependencies=())

        assert graph.startup_order() == ("a", "m", "z")

    def test_startup_order_raises_when_invalid(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=("missing",))

        with pytest.raises(ValueError, match="not valid"):
            graph.startup_order()

    def test_validate_returns_full_topological_order(self):
        graph = GovernanceDependencyGraph()
        graph.register("provider_registry", dependencies=())
        graph.register("metrics_bootstrap", dependencies=())
        graph.register(
            "delivery_runtime",
            dependencies=("provider_registry", "metrics_bootstrap"),
        )
        graph.register(
            "health_service", dependencies=("delivery_runtime",)
        )

        result = graph.validate()

        assert result.valid is True
        assert set(result.startup_order) == {
            "provider_registry",
            "metrics_bootstrap",
            "delivery_runtime",
            "health_service",
        }
        assert result.startup_order.index(
            "delivery_runtime"
        ) > result.startup_order.index("provider_registry")
        assert result.startup_order.index(
            "health_service"
        ) > result.startup_order.index("delivery_runtime")


# --- Missing dependency detection ---------------------------------------


class TestGovernanceDependencyGraphMissingDependencies:

    def test_missing_dependency_is_invalid(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=("ghost",))

        result = graph.validate()

        assert result.valid is False
        assert result.missing == ("ghost",)

    def test_missing_dependency_startup_order_is_empty(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=("ghost",))

        assert graph.validate().startup_order == ()

    def test_no_missing_dependencies_when_all_registered(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=())
        graph.register("b", dependencies=("a",))

        assert graph.validate().missing == ()


# --- Circular dependency detection --------------------------------------


class TestGovernanceDependencyGraphCycles:

    def test_direct_cycle_is_invalid(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=("b",))
        graph.register("b", dependencies=("a",))

        result = graph.validate()

        assert result.valid is False
        assert len(result.cycles) >= 1

    def test_self_dependency_is_a_cycle(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=("a",))

        result = graph.validate()

        assert result.valid is False
        assert result.cycles == (("a", "a"),)

    def test_indirect_cycle_is_detected(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=("b",))
        graph.register("b", dependencies=("c",))
        graph.register("c", dependencies=("a",))

        result = graph.validate()

        assert result.valid is False
        assert len(result.cycles) >= 1

    def test_acyclic_graph_has_no_cycles(self):
        graph = GovernanceDependencyGraph()
        graph.register("a", dependencies=())
        graph.register("b", dependencies=("a",))

        assert graph.validate().cycles == ()


# --- Bootstrap wiring ------------------------------------------------


class TestGovernanceBootstrap:

    def test_default_component_graph_is_valid(self):
        from backend.observability.deployment_governance_bootstrap import (
            build_governance_dependency_graph,
        )

        result = build_governance_dependency_graph().validate()

        assert result.valid is True

    def test_default_component_graph_includes_all_ten_components(self):
        from backend.observability.deployment_governance_bootstrap import (
            build_governance_dependency_graph,
        )

        names = {
            c.name
            for c in build_governance_dependency_graph().components()
        }

        assert names == {
            "provider_registry",
            "metrics_bootstrap",
            "logging_bootstrap",
            "delivery_runtime",
            "health_service",
            "readiness_service",
            "liveness_service",
            "diagnostics_service",
            "scheduler",
            "rollout_manager",
        }

    def test_bootstrap_governance_runtime_returns_valid_result(self):
        from backend.observability.deployment_governance_bootstrap import (
            bootstrap_governance_runtime,
        )

        result = bootstrap_governance_runtime()

        assert result.valid is True
        assert "delivery_runtime" in result.startup_order

    def test_bootstrap_aborts_on_invalid_graph(self, monkeypatch):
        import backend.observability.deployment_governance_bootstrap as bootstrap_module

        def _broken_graph():
            graph = GovernanceDependencyGraph()
            graph.register("a", dependencies=("missing",))
            return graph

        monkeypatch.setattr(
            bootstrap_module,
            "build_governance_dependency_graph",
            _broken_graph,
        )

        with pytest.raises(
            bootstrap_module.GovernanceBootstrapError,
            match="missing dependencies",
        ):
            bootstrap_module.bootstrap_governance_runtime()


# --- Health check adapter ------------------------------------------------


class TestDependencyGraphHealthCheck:

    def test_valid_result_is_healthy(self):
        from backend.observability.deployment_governance_health import (
            dependency_graph_health_check,
        )

        result = DependencyValidationResult(
            valid=True, startup_order=("a",), cycles=(), missing=()
        )

        assert dependency_graph_health_check(result) is True

    def test_invalid_result_is_unhealthy_with_reason(self):
        from backend.observability.deployment_governance_health import (
            dependency_graph_health_check,
        )

        result = DependencyValidationResult(
            valid=False, startup_order=(), cycles=(), missing=("ghost",)
        )

        healthy, message = dependency_graph_health_check(result)

        assert healthy is False
        assert "ghost" in message


# --- Persistence runtime wiring -----------------------------------------


class TestPersistenceRuntimeDependencyGraph:

    def test_returns_valid_graph(self):
        from backend.observability.deployment_governance_persistence import (
            build_deployment_governance_persistence,
        )

        runtime = build_deployment_governance_persistence()

        graph = runtime.build_integrity_dependency_graph()

        assert graph.validate().valid is True


# --- API endpoint ----------------------------------------------------------


def _setup_sqlite_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceDependenciesApi:

    def test_dependencies_endpoint_returns_full_payload(
        self, client, monkeypatch, tmp_path
    ) -> None:
        _setup_sqlite_env(monkeypatch, tmp_path, "api-dependencies.db")

        response = client.get("/governance/dependencies")

        assert response.status_code == 200

        payload = response.json()

        assert payload["valid"] is True
        assert payload["missing"] == []
        assert payload["cycles"] == []

        component_names = {c["name"] for c in payload["components"]}
        assert "delivery_runtime" in component_names
        assert "provider_registry" in component_names

        assert sorted(
            payload["dependency_map"]["delivery_runtime"]
        ) == [
            "logging_bootstrap",
            "metrics_bootstrap",
            "provider_registry",
        ]

        assert payload["startup_order"].index(
            "delivery_runtime"
        ) > payload["startup_order"].index("provider_registry")
