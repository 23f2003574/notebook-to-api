from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Callable, Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException

from .gateway_analytics import GatewayAnalytics
from .route_registry import RouteAlreadyRegisteredError, RouteRegistry


class GatewayNotRunningError(RuntimeError):
    pass


class GatewayAlreadyRunningError(RuntimeError):
    pass


class UnknownRouteError(KeyError):
    pass


class GatewayStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"


@dataclass(frozen=True)
class GatewayRequest:
    """A single request accepted for dispatch through the gateway."""

    route: str
    payload: dict
    request_id: str = field(default_factory=lambda: uuid4().hex)
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "payload": self.payload,
            "request_id": self.request_id,
            "received_at": self.received_at.isoformat(),
        }


@dataclass(frozen=True)
class GatewayResponse:
    """The result of dispatching a GatewayRequest to its handler."""

    request_id: str
    route: str
    result: Any
    dispatched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "route": self.route,
            "result": self.result,
            "dispatched_at": self.dispatched_at.isoformat(),
        }


class APIGateway:
    """Centralized entry point that manages lifecycle and dispatches requests to registered routes."""

    def __init__(self) -> None:
        self._status = GatewayStatus.STOPPED
        self._handlers: dict = {}
        self._dispatch_count = 0
        self._started_at: Optional[datetime] = None
        self._lock = Lock()
        self._route_registry = RouteRegistry()
        self._analytics = GatewayAnalytics()

    @property
    def route_registry(self) -> RouteRegistry:
        return self._route_registry

    @property
    def analytics(self) -> GatewayAnalytics:
        return self._analytics

    def register_route(
        self,
        route: str,
        handler: Callable[[dict], Any],
        *,
        methods: tuple = ("POST",),
        metadata: Optional[Any] = None,
    ) -> None:
        with self._lock:
            self._handlers[route] = handler
        try:
            self._route_registry.register(route, methods, metadata)
        except RouteAlreadyRegisteredError:
            pass

    def start(self) -> GatewayStatus:
        with self._lock:
            if self._status == GatewayStatus.RUNNING:
                raise GatewayAlreadyRunningError("gateway is already running")
            self._status = GatewayStatus.RUNNING
            self._started_at = datetime.now(timezone.utc)
            return self._status

    def stop(self) -> GatewayStatus:
        with self._lock:
            if self._status == GatewayStatus.STOPPED:
                raise GatewayNotRunningError("gateway is not running")
            self._status = GatewayStatus.STOPPED
            self._started_at = None
            return self._status

    def reset(self) -> None:
        """Idempotently stop the gateway and clear handlers, dispatch count, and analytics."""
        with self._lock:
            self._status = GatewayStatus.STOPPED
            self._started_at = None
            self._handlers.clear()
            self._dispatch_count = 0
        self._analytics.reset()

    def dispatch(self, route: str, payload: Optional[dict] = None) -> GatewayResponse:
        with self._lock:
            if self._status != GatewayStatus.RUNNING:
                raise GatewayNotRunningError("gateway must be started before dispatching requests")
            handler = self._handlers.get(route)
            if handler is None:
                raise UnknownRouteError(route)
            request = GatewayRequest(route=route, payload=payload or {})
            self._dispatch_count += 1

        analytics_id = self._analytics.record_request(route, "DISPATCH")
        try:
            result = handler(request.payload)
        except Exception:
            self._analytics.record_response(analytics_id, 500)
            raise
        self._analytics.record_response(analytics_id, 200)
        return GatewayResponse(request_id=request.request_id, route=route, result=result)

    def status(self) -> dict:
        with self._lock:
            return {
                "status": self._status.value,
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "dispatch_count": self._dispatch_count,
                "routes": sorted(self._handlers),
            }


_gateway = APIGateway()


def get_api_gateway() -> APIGateway:
    return _gateway


router = APIRouter(prefix="/gateway", tags=["gateway"])


@router.get("/status")
def gateway_status_endpoint(gateway: APIGateway = Depends(get_api_gateway)) -> dict:
    return gateway.status()


@router.post("/dispatch")
def gateway_dispatch_endpoint(
    payload: dict = Body(default={}),
    gateway: APIGateway = Depends(get_api_gateway),
) -> dict:
    route = payload.get("route", "")
    try:
        response = gateway.dispatch(route, payload.get("payload"))
    except GatewayNotRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UnknownRouteError:
        raise HTTPException(status_code=404, detail=f"unknown route: {route}")
    return response.to_dict()


@router.post("/start")
def gateway_start_endpoint(gateway: APIGateway = Depends(get_api_gateway)) -> dict:
    try:
        gateway.start()
    except GatewayAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return gateway.status()


@router.post("/stop")
def gateway_stop_endpoint(gateway: APIGateway = Depends(get_api_gateway)) -> dict:
    try:
        gateway.stop()
    except GatewayNotRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return gateway.status()
