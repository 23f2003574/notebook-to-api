import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.event_system import (
    EventType,
    HookAlreadyRegisteredError,
    HookEventSystem,
    HookRegistration,
    PluginEvent,
    UnknownHookError,
    UnknownSubscriptionError,
    get_hook_event_system,
    router as event_system_router,
)


@pytest.fixture
def system() -> HookEventSystem:
    return HookEventSystem()


@pytest.fixture
def client(system: HookEventSystem) -> TestClient:
    app = FastAPI()
    app.include_router(event_system_router)
    app.dependency_overrides[get_hook_event_system] = lambda: system
    return TestClient(app)


def test_register_hook_creates_entry(system: HookEventSystem):
    hook = system.register_hook("csv-exporter.before-export", "fires before export")

    assert hook == {"name": "csv-exporter.before-export", "description": "fires before export"}


def test_register_hook_duplicate_raises(system: HookEventSystem):
    system.register_hook("csv-exporter.before-export")

    with pytest.raises(HookAlreadyRegisteredError):
        system.register_hook("csv-exporter.before-export")


def test_unregister_hook_removes_entry(system: HookEventSystem):
    system.register_hook("csv-exporter.before-export")

    system.unregister_hook("csv-exporter.before-export")

    with pytest.raises(UnknownHookError):
        system.unregister_hook("csv-exporter.before-export")


def test_subscribe_to_unknown_hook_raises(system: HookEventSystem):
    with pytest.raises(UnknownHookError):
        system.subscribe("not-a-real-hook", "csv-exporter", lambda event: None)


def test_subscribe_to_builtin_event_type_succeeds(system: HookEventSystem):
    registration = system.subscribe(EventType.PLUGIN_LOADED, "csv-exporter", lambda event: None)

    assert isinstance(registration, HookRegistration)
    assert registration.event_type == "PluginLoaded"


def test_subscribe_to_custom_registered_hook_succeeds(system: HookEventSystem):
    system.register_hook("csv-exporter.before-export")

    registration = system.subscribe("csv-exporter.before-export", "csv-exporter", lambda event: None)

    assert registration.event_type == "csv-exporter.before-export"


def test_emit_dispatches_to_subscriber(system: HookEventSystem):
    received = []
    system.subscribe(EventType.PLUGIN_LOADED, "csv-exporter", received.append)

    event = system.emit(EventType.PLUGIN_LOADED, payload={"plugin": "csv-exporter"}, source="loader")

    assert isinstance(event, PluginEvent)
    assert received == [event]
    assert event.payload == {"plugin": "csv-exporter"}


def test_emit_with_no_subscribers_still_records_event(system: HookEventSystem):
    event = system.emit(EventType.REQUEST_STARTED)

    assert system.list_events() == [event]


def test_emit_dispatches_in_priority_order(system: HookEventSystem):
    order = []
    system.subscribe(EventType.PLUGIN_LOADED, "b", lambda event: order.append("b"), priority=10)
    system.subscribe(EventType.PLUGIN_LOADED, "a", lambda event: order.append("a"), priority=1)
    system.subscribe(EventType.PLUGIN_LOADED, "c", lambda event: order.append("c"), priority=5)

    system.emit(EventType.PLUGIN_LOADED)

    assert order == ["a", "c", "b"]


def test_emit_applies_filter_fn(system: HookEventSystem):
    received = []
    system.subscribe(
        EventType.PLUGIN_LOADED,
        "csv-exporter",
        received.append,
        filter_fn=lambda event: event.payload.get("plugin") == "csv-exporter",
    )

    system.emit(EventType.PLUGIN_LOADED, payload={"plugin": "other-plugin"})
    system.emit(EventType.PLUGIN_LOADED, payload={"plugin": "csv-exporter"})

    assert len(received) == 1
    assert received[0].payload["plugin"] == "csv-exporter"


def test_unsubscribe_stops_dispatch(system: HookEventSystem):
    received = []
    registration = system.subscribe(EventType.PLUGIN_LOADED, "csv-exporter", received.append)

    system.unsubscribe(registration.hook_id)
    system.emit(EventType.PLUGIN_LOADED)

    assert received == []


def test_unsubscribe_unknown_id_raises(system: HookEventSystem):
    with pytest.raises(UnknownSubscriptionError):
        system.unsubscribe("does-not-exist")


def test_emit_dispatches_async_handler_when_no_running_loop(system: HookEventSystem):
    received = []

    async def handler(event):
        received.append(event)

    system.subscribe(EventType.PLUGIN_LOADED, "csv-exporter", handler)

    system.emit(EventType.PLUGIN_LOADED)

    assert len(received) == 1


def test_aemit_awaits_both_sync_and_async_handlers():
    system = HookEventSystem()
    received = []

    async def async_handler(event):
        received.append(("async", event.event_type))

    def sync_handler(event):
        received.append(("sync", event.event_type))

    system.subscribe(EventType.PLUGIN_LOADED, "a", sync_handler, priority=0)
    system.subscribe(EventType.PLUGIN_LOADED, "b", async_handler, priority=1)

    asyncio.run(system.aemit(EventType.PLUGIN_LOADED))

    assert received == [("sync", "PluginLoaded"), ("async", "PluginLoaded")]


def test_list_events_filters_by_event_type(system: HookEventSystem):
    system.emit(EventType.PLUGIN_LOADED)
    system.emit(EventType.REQUEST_STARTED)

    events = system.list_events(event_type=EventType.REQUEST_STARTED.value)

    assert [event.event_type for event in events] == ["RequestStarted"]


def test_list_events_filters_by_source(system: HookEventSystem):
    system.emit(EventType.PLUGIN_LOADED, source="csv-exporter")
    system.emit(EventType.PLUGIN_LOADED, source="json-exporter")

    events = system.list_events(source="csv-exporter")

    assert [event.source for event in events] == ["csv-exporter"]


def test_list_events_respects_limit(system: HookEventSystem):
    for _ in range(5):
        system.emit(EventType.REQUEST_STARTED)

    events = system.list_events(limit=2)

    assert len(events) == 2


def test_api_register_hook_then_delete(client: TestClient):
    response = client.post("/plugins/hooks", json={"name": "custom.hook", "description": "d"})
    assert response.status_code == 201

    delete_response = client.delete("/plugins/hooks/custom.hook")
    assert delete_response.status_code == 204

    assert client.delete("/plugins/hooks/custom.hook").status_code == 404


def test_api_register_hook_duplicate_returns_409(client: TestClient):
    client.post("/plugins/hooks", json={"name": "custom.hook"})

    response = client.post("/plugins/hooks", json={"name": "custom.hook"})

    assert response.status_code == 409


def test_api_list_events(client: TestClient, system: HookEventSystem):
    system.emit(EventType.COMPILATION_FINISHED, payload={"notebook": "demo.ipynb"})

    response = client.get("/plugins/events")

    assert response.status_code == 200
    assert response.json()[0]["event_type"] == "CompilationFinished"


def test_api_list_events_filters_by_event_type(client: TestClient, system: HookEventSystem):
    system.emit(EventType.PLUGIN_LOADED)
    system.emit(EventType.REQUEST_STARTED)

    response = client.get("/plugins/events", params={"event_type": "RequestStarted"})

    assert [event["event_type"] for event in response.json()] == ["RequestStarted"]
