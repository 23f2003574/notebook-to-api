from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .rate_limiter import RateLimiter, RateLimitExceededError, UnknownRateLimitClientError, get_rate_limiter
from ..performance.response_cache import CacheContext, get_response_cache_middleware
from ..performance.compression import (
    CompressionAlgorithm,
    CompressionEngine,
    UnsupportedAlgorithmError,
    get_compression_engine,
)

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


class RateLimitingMiddleware:
    """Built-in middleware that throttles requests per client using a RateLimiter."""

    def __init__(self, limiter: RateLimiter, client_key: str = "client") -> None:
        self.limiter = limiter
        self.client_key = client_key

    def before(self, context: MiddlewareContext) -> None:
        client = context.payload.get(self.client_key, "anonymous")
        try:
            self.limiter.consume(client)
        except RateLimitExceededError as exc:
            context.short_circuited = True
            context.response = {"error": "rate_limited", "retry_after": exc.retry_after}
        except UnknownRateLimitClientError:
            pass


class ResponseCachingMiddleware:
    """Built-in middleware that serves cached GET responses and stores fresh ones."""

    def __init__(self, *, ttl_seconds: Optional[float] = None) -> None:
        self.ttl_seconds = ttl_seconds

    def before(self, context: MiddlewareContext) -> None:
        cache = get_response_cache_middleware()
        cache_context = CacheContext(
            method=context.method,
            path=context.path,
            query_params=context.payload.get("query_params", {}),
        )
        cached = cache.before_request(cache_context)
        if cached is not None:
            context.short_circuited = True
            context.response = cached.body
            context.state["cache_status"] = "hit"
        else:
            context.state["cache_status"] = "miss"

    def after(self, context: MiddlewareContext) -> None:
        if context.state.get("cache_status") == "hit":
            return
        cache = get_response_cache_middleware()
        cache_context = CacheContext(
            method=context.method,
            path=context.path,
            query_params=context.payload.get("query_params", {}),
            status_code=context.payload.get("status_code", 200),
            body=context.response,
            ttl_seconds=self.ttl_seconds,
        )
        cache.after_response(cache_context)


class CompressionEngineMiddleware:
    """Built-in middleware that negotiates and applies real payload compression."""

    def __init__(
        self,
        *,
        threshold_bytes: Optional[int] = None,
        level: Optional[int] = None,
        algorithm: Optional[CompressionAlgorithm] = None,
    ) -> None:
        self.threshold_bytes = threshold_bytes
        self.level = level
        self.algorithm = algorithm

    def after(self, context: MiddlewareContext) -> None:
        engine = get_compression_engine()
        chosen = self.algorithm or engine.negotiate(context.payload.get("accept_encoding", ""))
        body = context.response if context.response is not None else context.payload
        result = engine.compress(
            str(body), algorithm=chosen, threshold_bytes=self.threshold_bytes, level=self.level
        )
        context.state["compression"] = result.to_dict()


def _build_authentication(config: dict):
    return AuthenticationMiddleware(config.get("required_token", "")).before, None


def _build_logging(config: dict):
    instance = LoggingMiddleware()
    return instance.before, instance.after


def _build_cors(config: dict):
    return None, CORSMiddleware(tuple(config.get("allowed_origins", ["*"]))).after


def _build_compression(config: dict):
    return None, CompressionMiddleware(config.get("minimum_size", 0)).after


def _build_rate_limiting(config: dict):
    return RateLimitingMiddleware(get_rate_limiter(), client_key=config.get("client_key", "client")).before, None


def _build_response_caching(config: dict):
    instance = ResponseCachingMiddleware(ttl_seconds=config.get("ttl_seconds"))
    return instance.before, instance.after


def _build_compression_engine(config: dict):
    instance = CompressionEngineMiddleware(
        threshold_bytes=config.get("threshold_bytes"),
        level=config.get("level"),
    )
    return None, instance.after


BUILTIN_MIDDLEWARE_FACTORIES = {
    "authentication": _build_authentication,
    "logging": _build_logging,
    "cors": _build_cors,
    "compression": _build_compression,
    "rate_limiting": _build_rate_limiting,
    "response_caching": _build_response_caching,
    "compression_engine": _build_compression_engine,
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


compression_router = APIRouter(prefix="/performance/compression", tags=["compression-engine"])


@compression_router.get("")
def get_compression_config_endpoint(
    engine: CompressionEngine = Depends(get_compression_engine),
) -> dict:
    return {
        **engine.profile.to_dict(),
        "supported_algorithms": [alg.value for alg in CompressionAlgorithm if engine.supports(alg)],
    }


@compression_router.post("/test")
def test_compression_endpoint(
    payload: dict = Body(default={}),
    engine: CompressionEngine = Depends(get_compression_engine),
) -> dict:
    data = payload.get("data", "")
    accept_encoding = payload.get("accept_encoding")
    if "algorithm" in payload:
        try:
            algorithm = CompressionAlgorithm(payload["algorithm"])
        except ValueError:
            raise HTTPException(status_code=422, detail="unknown compression algorithm")
    else:
        algorithm = engine.negotiate(accept_encoding or "")
    try:
        result = engine.compress(data, algorithm=algorithm)
    except UnsupportedAlgorithmError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@compression_router.get("/stats")
def compression_stats_endpoint(
    engine: CompressionEngine = Depends(get_compression_engine),
) -> dict:
    return engine.stats()
