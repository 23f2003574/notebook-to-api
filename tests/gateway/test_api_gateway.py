import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.api_gateway import (
    APIGateway,
    GatewayAlreadyRunningError,
    GatewayNotRunningError,
    GatewayResponse,
    GatewayStatus,
    UnknownRouteError,
    get_api_gateway,
    router as api_gateway_router,
)


@pytest.fixture
def gateway() -> APIGateway:
    gw = APIGateway()
    gw.register_route("echo", lambda payload: payload)
    return gw


@pytest.fixture
def client(gateway: APIGateway) -> TestClient:
    app = FastAPI()
    app.include_router(api_gateway_router)
    app.dependency_overrides[get_api_gateway] = lambda: gateway
    return TestClient(app)


def test_start_transitions_to_running(gateway: APIGateway):
    status = gateway.start()

    assert status == GatewayStatus.RUNNING
    assert gateway.status()["status"] == "running"


def test_start_twice_raises(gateway: APIGateway):
    gateway.start()

    with pytest.raises(GatewayAlreadyRunningError):
        gateway.start()


def test_stop_transitions_to_stopped(gateway: APIGateway):
    gateway.start()

    status = gateway.stop()

    assert status == GatewayStatus.STOPPED
    assert gateway.status()["status"] == "stopped"


def test_stop_without_start_raises(gateway: APIGateway):
    with pytest.raises(GatewayNotRunningError):
        gateway.stop()


def test_dispatch_requires_running_gateway(gateway: APIGateway):
    with pytest.raises(GatewayNotRunningError):
        gateway.dispatch("echo", {"value": 1})


def test_dispatch_routes_to_handler(gateway: APIGateway):
    gateway.start()

    response = gateway.dispatch("echo", {"value": 42})

    assert isinstance(response, GatewayResponse)
    assert response.route == "echo"
    assert response.result == {"value": 42}


def test_dispatch_unknown_route_raises(gateway: APIGateway):
    gateway.start()

    with pytest.raises(UnknownRouteError):
        gateway.dispatch("does-not-exist", {})


def test_status_reports_dispatch_count(gateway: APIGateway):
    gateway.start()
    gateway.dispatch("echo", {"value": 1})
    gateway.dispatch("echo", {"value": 2})

    status = gateway.status()

    assert status["dispatch_count"] == 2
    assert status["routes"] == ["echo"]


def test_api_status_reports_stopped_by_default(client: TestClient):
    response = client.get("/gateway/status")

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"


def test_api_start_then_status_reports_running(client: TestClient):
    start_response = client.post("/gateway/start")
    assert start_response.status_code == 200

    status_response = client.get("/gateway/status")
    assert status_response.json()["status"] == "running"


def test_api_start_twice_returns_409(client: TestClient):
    client.post("/gateway/start")

    response = client.post("/gateway/start")

    assert response.status_code == 409


def test_api_dispatch_before_start_returns_409(client: TestClient):
    response = client.post("/gateway/dispatch", json={"route": "echo", "payload": {}})

    assert response.status_code == 409


def test_api_dispatch_unknown_route_returns_404(client: TestClient):
    client.post("/gateway/start")

    response = client.post("/gateway/dispatch", json={"route": "missing", "payload": {}})

    assert response.status_code == 404


def test_api_dispatch_returns_result(client: TestClient):
    client.post("/gateway/start")

    response = client.post(
        "/gateway/dispatch", json={"route": "echo", "payload": {"value": 7}}
    )

    assert response.status_code == 200
    assert response.json()["result"] == {"value": 7}


def test_api_stop_returns_stopped_status(client: TestClient):
    client.post("/gateway/start")

    response = client.post("/gateway/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"


def test_api_stop_without_start_returns_409(client: TestClient):
    response = client.post("/gateway/stop")

    assert response.status_code == 409
