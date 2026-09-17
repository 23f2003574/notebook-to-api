from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.observability.deployment_governance_event_bus import (
    GovernanceEvent,
    GovernanceEventBus,
)
from backend.observability.deployment_governance_event_router import (
    EventRoute,
    GovernanceEventRouter,
    RouteMatch,
    route_matches,
)

BASE_TIME = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _event(event_type="component_started", source="a", event_id="1"):
    return GovernanceEvent(
        event_id=event_id,
        event_type=event_type,
        source=source,
        payload={},
        occurred_at=BASE_TIME,
    )


# --- Model -------------------------------------------------------------


class TestEventRoute:

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            EventRoute(
                name="",
                event_types=("*",),
                sources=("*",),
                priority=0,
            )

    def test_rejects_empty_event_types(self):
        with pytest.raises(ValueError, match="event_types must not be empty"):
            EventRoute(
                name="a", event_types=(), sources=("*",), priority=0
            )

    def test_rejects_empty_sources(self):
        with pytest.raises(ValueError, match="sources must not be empty"):
            EventRoute(
                name="a", event_types=("*",), sources=(), priority=0
            )

    def test_defaults_to_enabled(self):
        route = EventRoute(
            name="a", event_types=("*",), sources=("*",), priority=0
        )

        assert route.enabled is True

    def test_to_dict(self):
        route = EventRoute(
            name="a",
            event_types=("x",),
            sources=("y",),
            priority=3,
            enabled=False,
        )

        assert route.to_dict() == {
            "name": "a",
            "event_types": ["x"],
            "sources": ["y"],
            "priority": 3,
            "enabled": False,
        }


class TestRouteMatch:

    def test_rejects_matched_with_reason(self):
        with pytest.raises(
            ValueError, match="reason must not be set when matched is True"
        ):
            RouteMatch(route="a", matched=True, reason="boom")

    def test_rejects_not_matched_without_reason(self):
        with pytest.raises(
            ValueError, match="reason must be set when matched is False"
        ):
            RouteMatch(route="a", matched=False, reason=None)

    def test_to_dict(self):
        match = RouteMatch(route="a", matched=False, reason="nope")

        assert match.to_dict() == {
            "route": "a",
            "matched": False,
            "reason": "nope",
        }


# --- Register route --------------------------------------------------


class TestRegisterRoute:

    def test_register_returns_route(self):
        router = GovernanceEventRouter()

        route = router.register_route(
            "a", event_types=("component_started",), sources=("x",)
        )

        assert route.name == "a"
        assert route.event_types == ("component_started",)

    def test_register_defaults_to_wildcard(self):
        router = GovernanceEventRouter()

        route = router.register_route("a")

        assert route.event_types == ("*",)
        assert route.sources == ("*",)

    def test_registered_route_appears_in_routes(self):
        router = GovernanceEventRouter()
        router.register_route("a")

        assert [r.name for r in router.routes()] == ["a"]


# --- Duplicate route rejection --------------------------------------


def test_duplicate_route_name_rejected():
    router = GovernanceEventRouter()
    router.register_route("a")

    with pytest.raises(ValueError, match="already registered"):
        router.register_route("a")


def test_remove_unknown_route_raises():
    router = GovernanceEventRouter()

    with pytest.raises(KeyError):
        router.remove_route("ghost")


def test_remove_route_removes_it():
    router = GovernanceEventRouter()
    router.register_route("a")
    router.remove_route("a")

    assert router.routes() == ()


# --- Priority ordering -----------------------------------------------


class TestPriorityOrdering:

    def test_routes_ordered_by_priority_then_name(self):
        router = GovernanceEventRouter()
        router.register_route("z", priority=1)
        router.register_route("a", priority=1)
        router.register_route("m", priority=0)

        assert [r.name for r in router.routes()] == ["m", "a", "z"]

    def test_route_matches_are_ordered_the_same_way(self):
        router = GovernanceEventRouter()
        router.register_route("z", priority=1)
        router.register_route("a", priority=1)
        router.register_route("m", priority=0)

        matches = router.route(_event())

        assert [m.route for m in matches] == ["m", "a", "z"]

    def test_ordering_independent_of_registration_order(self):
        router_a = GovernanceEventRouter()
        router_a.register_route("b", priority=1)
        router_a.register_route("a", priority=1)

        router_b = GovernanceEventRouter()
        router_b.register_route("a", priority=1)
        router_b.register_route("b", priority=1)

        assert [r.name for r in router_a.routes()] == [
            r.name for r in router_b.routes()
        ]


# --- Wildcard matching -----------------------------------------------


class TestWildcardMatching:

    def test_wildcard_event_type_matches_anything(self):
        route = EventRoute(
            name="a", event_types=("*",), sources=("x",), priority=0
        )

        assert route_matches(route, event_type="anything", source="x")

    def test_wildcard_source_matches_anything(self):
        route = EventRoute(
            name="a", event_types=("x",), sources=("*",), priority=0
        )

        assert route_matches(route, event_type="x", source="anything")

    def test_non_wildcard_requires_exact_match(self):
        route = EventRoute(
            name="a",
            event_types=("component_started",),
            sources=("x",),
            priority=0,
        )

        assert not route_matches(
            route, event_type="component_stopped", source="x"
        )

    def test_router_route_reports_wildcard_match(self):
        router = GovernanceEventRouter()
        router.register_route("a", event_types=("*",), sources=("*",))

        matches = router.route(_event())

        assert matches[0].matched is True


# --- Source filtering --------------------------------------------------


class TestSourceFiltering:

    def test_route_only_matches_registered_source(self):
        router = GovernanceEventRouter()
        router.register_route("a", sources=("provider_registry",))

        match = router.route(_event(source="provider_registry"))[0]
        assert match.matched is True

        no_match = router.route(_event(source="other"))[0]
        assert no_match.matched is False
        assert "does not match" in no_match.reason


# --- Enable / disable ----------------------------------------------------


class TestEnableDisable:

    def test_disable_route_is_skipped(self):
        router = GovernanceEventRouter()
        router.register_route("a")
        router.disable_route("a")

        match = router.route(_event())[0]

        assert match.matched is False
        assert match.reason == "route is disabled"

    def test_enable_re_enables_route(self):
        router = GovernanceEventRouter()
        router.register_route("a")
        router.disable_route("a")
        router.enable_route("a")

        match = router.route(_event())[0]

        assert match.matched is True

    def test_disable_is_idempotent(self):
        router = GovernanceEventRouter()
        router.register_route("a")

        router.disable_route("a")
        router.disable_route("a")

        assert router.routes()[0].enabled is False

    def test_enable_unknown_route_raises(self):
        router = GovernanceEventRouter()

        with pytest.raises(KeyError):
            router.enable_route("ghost")

    def test_disable_unknown_route_raises(self):
        router = GovernanceEventRouter()

        with pytest.raises(KeyError):
            router.disable_route("ghost")

    def test_routes_reports_all_routes_regardless_of_enabled_state(
        self,
    ):
        router = GovernanceEventRouter()
        router.register_route("a")
        router.register_route("b")
        router.disable_route("b")

        assert {r.name for r in router.routes()} == {"a", "b"}


# --- Multiple route fan-out ----------------------------------------


class TestMultipleRouteFanOut:

    def test_multiple_matching_routes_all_reported(self):
        router = GovernanceEventRouter()
        router.register_route("a", event_types=("component_started",))
        router.register_route("b", event_types=("*",))

        matches = router.route(_event(event_type="component_started"))

        assert {m.route for m in matches if m.matched} == {"a", "b"}

    def test_handle_event_notifies_every_matched_route(self):
        received = []

        router = GovernanceEventRouter(
            on_match=lambda name, event: received.append(name)
        )
        router.register_route("a", event_types=("component_started",))
        router.register_route("b", event_types=("*",))
        router.register_route(
            "c", event_types=("component_stopped",)
        )

        router.handle_event(_event(event_type="component_started"))

        assert set(received) == {"a", "b"}

    def test_handler_failure_is_isolated(self):
        received = []

        def _on_match(name, event):
            if name == "boom":
                raise RuntimeError("boom")
            received.append(name)

        router = GovernanceEventRouter(on_match=_on_match)
        router.register_route("boom", priority=0)
        router.register_route("ok", priority=1)

        router.handle_event(_event())

        assert received == ["ok"]


# --- Immutable events --------------------------------------------------


class TestRoutingDoesNotMutateEvents:

    def test_event_unchanged_after_routing(self):
        router = GovernanceEventRouter()
        router.register_route("a")

        event = _event()
        original_payload = dict(event.payload)

        router.route(event)

        assert dict(event.payload) == original_payload
        assert event.event_type == "component_started"

    def test_event_payload_still_immutable_after_routing(self):
        router = GovernanceEventRouter()
        router.register_route("a")

        event = _event()
        router.route(event)

        with pytest.raises(TypeError):
            event.payload["x"] = 1


# --- clear() -----------------------------------------------------------


def test_clear_removes_every_route():
    router = GovernanceEventRouter()
    router.register_route("a")
    router.register_route("b")

    router.clear()

    assert router.routes() == ()


# --- Event bus wiring --------------------------------------------------


class TestEventBusRouteThrough:

    def test_route_through_wires_router_as_wildcard_subscriber(self):
        bus = GovernanceEventBus()
        received = []
        router = GovernanceEventRouter(
            on_match=lambda name, event: received.append(name)
        )
        router.register_route("a")

        bus.route_through(router)
        bus.publish("component_started", source="x")

        assert received == ["a"]


# --- Event history integration -------------------------------------


class TestEventHistoryMatching:

    def test_matching_finds_historical_events_for_a_route(self):
        from backend.observability.deployment_governance_event_history import (
            GovernanceEventHistory,
        )

        history = GovernanceEventHistory()
        history.append(_event(event_type="component_started", event_id="1"))
        history.append(_event(event_type="component_stopped", event_id="2"))

        route = EventRoute(
            name="a",
            event_types=("component_started",),
            sources=("*",),
            priority=0,
        )

        results = history.matching(route)

        assert [r.event.event_id for r in results] == ["1"]

    def test_matching_ignores_enabled_state(self):
        from backend.observability.deployment_governance_event_history import (
            GovernanceEventHistory,
        )

        history = GovernanceEventHistory()
        history.append(_event())

        disabled_route = EventRoute(
            name="a",
            event_types=("*",),
            sources=("*",),
            priority=0,
            enabled=False,
        )

        assert len(history.matching(disabled_route)) == 1


# --- Default routes ------------------------------------------------------


class TestDefaultGovernanceEventRouter:

    def test_default_router_has_expected_routes(self):
        from backend.observability.deployment_governance_event_router import (
            build_default_governance_event_router,
        )

        names = {r.name for r in build_default_governance_event_router().routes()}

        assert names == {
            "failure_events_to_alert_pipeline",
            "lifecycle_to_logging",
            "lifecycle_to_metrics",
            "health_to_diagnostics",
            "readiness_to_diagnostics",
            "provider_events_to_metrics",
        }

    def test_failure_events_route_matches_component_failed(self):
        from backend.observability.deployment_governance_event_router import (
            build_default_governance_event_router,
        )

        router = build_default_governance_event_router()

        matches = router.route(_event(event_type="component_failed"))

        failure_match = next(
            m for m in matches if m.route == "failure_events_to_alert_pipeline"
        )
        assert failure_match.matched is True


# --- Singleton -----------------------------------------------------------


class TestEventRouterSingleton:

    def test_get_event_router_returns_same_instance(self):
        from backend.observability.deployment_governance_event_router import (
            get_event_router,
        )

        assert get_event_router() is get_event_router()

    def test_rewires_after_bus_clear(self):
        from backend.observability.deployment_governance_event_bus import (
            get_event_bus,
        )
        from backend.observability.deployment_governance_event_router import (
            get_event_router,
        )

        router = get_event_router()
        get_event_bus().clear()

        received = []
        router.register_route(
            "test_rewire_probe",
            event_types=("*",),
            sources=("probe",),
            priority=-1,
        )

        try:
            get_event_router()  # should re-wire

            def _capture(name, event):
                if name == "test_rewire_probe":
                    received.append(name)

            router._on_match = _capture

            get_event_bus().publish("component_started", source="probe")

            assert received == ["test_rewire_probe"]

        finally:
            router.remove_route("test_rewire_probe")
            get_event_bus().clear()


# --- API endpoints -----------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    from backend.dashboard import app

    return TestClient(app)


class TestGovernanceRoutesApi:

    def test_get_routes_returns_default_routes(self, client) -> None:
        response = client.get("/governance/routes")

        assert response.status_code == 200

        names = {route["name"] for route in response.json()}

        assert "lifecycle_to_logging" in names

    def test_post_route_registers_and_returns_it(self, client) -> None:
        response = client.post(
            "/governance/routes",
            params={
                "name": "api_test_route",
                "event_types": ["component_started"],
                "sources": ["x"],
                "priority": 7,
            },
        )

        try:
            assert response.status_code == 200

            payload = response.json()

            assert payload["name"] == "api_test_route"
            assert payload["priority"] == 7

        finally:
            client.delete("/governance/routes/api_test_route")

    def test_post_duplicate_route_returns_409(self, client) -> None:
        client.post(
            "/governance/routes", params={"name": "api_dup_route"}
        )

        try:
            response = client.post(
                "/governance/routes", params={"name": "api_dup_route"}
            )

            assert response.status_code == 409

        finally:
            client.delete("/governance/routes/api_dup_route")

    def test_patch_route_disables_it(self, client) -> None:
        client.post(
            "/governance/routes", params={"name": "api_patch_route"}
        )

        try:
            response = client.patch(
                "/governance/routes/api_patch_route",
                params={"enabled": False},
            )

            assert response.status_code == 200
            assert response.json()["enabled"] is False

        finally:
            client.delete("/governance/routes/api_patch_route")

    def test_patch_unknown_route_returns_404(self, client) -> None:
        response = client.patch(
            "/governance/routes/does_not_exist", params={"enabled": True}
        )

        assert response.status_code == 404

    def test_delete_route_removes_it(self, client) -> None:
        client.post(
            "/governance/routes", params={"name": "api_delete_route"}
        )

        response = client.delete("/governance/routes/api_delete_route")

        assert response.status_code == 200
        assert response.json() == {"removed": "api_delete_route"}

        names = {
            route["name"]
            for route in client.get("/governance/routes").json()
        }
        assert "api_delete_route" not in names

    def test_delete_unknown_route_returns_404(self, client) -> None:
        response = client.delete("/governance/routes/does_not_exist")

        assert response.status_code == 404
