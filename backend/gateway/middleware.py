from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

BeforeHook = Callable[["MiddlewareContext"], None]
AfterHook = Callable[["MiddlewareContext"], None]


class MiddlewareAlreadyRegisteredError(ValueError):
    pass


class UnknownMiddlewareError(KeyError):
    pass


@dataclass
class MiddlewareContext:
    """Mutable state threaded through a middleware chain for a single request."""

    path: str
    method: str = ""
    payload: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    short_circuited: bool = False
    response: Optional[Any] = None
    executed: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "method": self.method,
            "payload": self.payload,
            "state": self.state,
            "short_circuited": self.short_circuited,
            "response": self.response,
            "executed": list(self.executed),
            "timings": dict(self.timings),
        }


@dataclass(frozen=True)
class Middleware:
    """A single entry in the middleware chain."""

    name: str
    priority: int
    before: Optional[BeforeHook] = None
    after: Optional[AfterHook] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "priority": self.priority,
            "has_before": self.before is not None,
            "has_after": self.after is not None,
        }


class MiddlewarePipeline:
    """Executes registered middleware in order, before and after routing."""

    def __init__(self) -> None:
        self._middlewares: dict = {}
        self._lock = Lock()
        self._sequence = 0

    def register(
        self,
        name: str,
        before: Optional[BeforeHook] = None,
        after: Optional[AfterHook] = None,
        *,
        priority: Optional[int] = None,
    ) -> Middleware:
        if not name:
            raise ValueError("middleware name is required")
        if before is None and after is None:
            raise ValueError("at least one of before/after is required")
        with self._lock:
            if name in self._middlewares:
                raise MiddlewareAlreadyRegisteredError(f"{name} is already registered")
            if priority is None:
                priority = self._sequence
            self._sequence += 1
            middleware = Middleware(name=name, priority=priority, before=before, after=after)
            self._middlewares[name] = middleware
        return middleware

    def remove(self, name: str) -> None:
        with self._lock:
            if name not in self._middlewares:
                raise UnknownMiddlewareError(name)
            del self._middlewares[name]

    def list_middleware(self) -> list:
        with self._lock:
            return sorted(self._middlewares.values(), key=lambda middleware: (middleware.priority, middleware.name))

    def execute_before(self, context: MiddlewareContext) -> MiddlewareContext:
        for middleware in self.list_middleware():
            if context.short_circuited:
                break
            if middleware.before is not None:
                start = perf_counter()
                middleware.before(context)
                context.timings[f"{middleware.name}.before"] = perf_counter() - start
            context.executed.append(middleware.name)
        return context

    def execute_after(self, context: MiddlewareContext) -> MiddlewareContext:
        for name in reversed(context.executed):
            middleware = self._middlewares.get(name)
            if middleware is None or middleware.after is None:
                continue
            start = perf_counter()
            middleware.after(context)
            context.timings[f"{name}.after"] = perf_counter() - start
        return context


class AuthenticationMiddleware:
    """Built-in middleware that short-circuits requests missing a valid token."""

    def __init__(self, required_token: str) -> None:
        self.required_token = required_token

    def before(self, context: MiddlewareContext) -> None:
        if context.payload.get("token") != self.required_token:
            context.short_circuited = True
            context.response = {"error": "unauthorized"}


class LoggingMiddleware:
    """Built-in middleware that records a before/after entry for each request."""

    def __init__(self) -> None:
        self.entries: list = []

    def before(self, context: MiddlewareContext) -> None:
        self.entries.append({"phase": "before", "path": context.path, "method": context.method})

    def after(self, context: MiddlewareContext) -> None:
        self.entries.append({"phase": "after", "path": context.path, "method": context.method})


class CORSMiddleware:
    """Built-in middleware that attaches CORS headers to the request state."""

    def __init__(self, allowed_origins: tuple = ("*",)) -> None:
        self.allowed_origins = tuple(allowed_origins)

    def after(self, context: MiddlewareContext) -> None:
        context.state["cors_headers"] = {"Access-Control-Allow-Origin": ",".join(self.allowed_origins)}


class CompressionMiddleware:
    """Built-in middleware that flags responses at or above a size threshold for compression."""

    def __init__(self, minimum_size: int = 0) -> None:
        self.minimum_size = minimum_size

    def after(self, context: MiddlewareContext) -> None:
        context.state["compressed"] = len(str(context.payload)) >= self.minimum_size


def _build_authentication(config: dict):
    return AuthenticationMiddleware(config.get("required_token", "")).before, None


def _build_logging(config: dict):
    instance = LoggingMiddleware()
    return instance.before, instance.after


def _build_cors(config: dict):
    return None, CORSMiddleware(tuple(config.get("allowed_origins", ["*"]))).after


def _build_compression(config: dict):
    return None, CompressionMiddleware(config.get("minimum_size", 0)).after


BUILTIN_MIDDLEWARE_FACTORIES = {
    "authentication": _build_authentication,
    "logging": _build_logging,
    "cors": _build_cors,
    "compression": _build_compression,
}


_middleware_pipeline = MiddlewarePipeline()


def get_middleware_pipeline() -> MiddlewarePipeline:
    return _middleware_pipeline


router = APIRouter(prefix="/gateway", tags=["gateway-middleware"])


@router.post("/middleware", status_code=201)
def register_middleware_endpoint(
    payload: dict = Body(default={}),
    pipeline: MiddlewarePipeline = Depends(get_middleware_pipeline),
) -> dict:
    middleware_type = payload.get("type", "")
    factory = BUILTIN_MIDDLEWARE_FACTORIES.get(middleware_type)
    if factory is None:
        raise HTTPException(status_code=422, detail=f"unknown middleware type: {middleware_type}")
    name = payload.get("name") or middleware_type
    before, after = factory(payload.get("config") or {})
    try:
        middleware = pipeline.register(name, before=before, after=after, priority=payload.get("priority"))
    except MiddlewareAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return middleware.to_dict()


@router.get("/middleware")
def list_middleware_endpoint(pipeline: MiddlewarePipeline = Depends(get_middleware_pipeline)) -> list:
    return [middleware.to_dict() for middleware in pipeline.list_middleware()]


@router.delete("/middleware/{middleware}", status_code=204)
def remove_middleware_endpoint(
    middleware: str,
    pipeline: MiddlewarePipeline = Depends(get_middleware_pipeline),
) -> None:
    try:
        pipeline.remove(middleware)
    except UnknownMiddlewareError:
        raise HTTPException(status_code=404, detail="unknown middleware")
