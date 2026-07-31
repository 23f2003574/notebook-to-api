from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .middleware import MiddlewareContext, MiddlewarePipeline, get_middleware_pipeline
from .request_validation import RequestValidationEngine, UnknownValidationRuleError, get_validation_engine
from .route_registry import (
    InvalidMethodError,
    Route,
    RouteNotFoundError,
    RouteRegistry,
    get_route_registry,
    match_path_template,
)

FallbackHandler = Callable[[str, Optional[str]], "RoutingResult"]


@dataclass(frozen=True)
class RouteMatch:
    """The result of successfully matching a concrete path to a registered route template."""

    path: str
    template: str
    method: Optional[str]
    params: dict
    route: Route

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "template": self.template,
            "method": self.method,
            "params": self.params,
            "route": self.route.to_dict(),
        }


@dataclass(frozen=True)
class RoutingResult:
    """The outcome of routing a request, whether matched or resolved via fallback."""

    matched: bool
    path: str
    method: Optional[str]
    route: Optional[Route]
    params: dict = field(default_factory=dict)
    reason: str = "matched"
    middleware_response: Optional[Any] = None
    validation_errors: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "path": self.path,
            "method": self.method,
            "route": self.route.to_dict() if self.route else None,
            "params": self.params,
            "reason": self.reason,
            "middleware_response": self.middleware_response,
            "validation_errors": list(self.validation_errors),
        }


class RequestRouter:
    """Resolves incoming requests to registered routes, extracting path parameters."""

    def __init__(
        self,
        registry: RouteRegistry,
        fallback_handler: Optional[FallbackHandler] = None,
        middleware_pipeline: Optional[MiddlewarePipeline] = None,
        validation_engine: Optional[RequestValidationEngine] = None,
    ) -> None:
        self._registry = registry
        self._fallback_handler = fallback_handler
        self._middleware_pipeline = middleware_pipeline
        self._validation_engine = validation_engine

    def match(self, path: str, method: Optional[str] = None) -> RouteMatch:
        structural_match: Optional[Route] = None
        structural_params: dict = {}
        for route in self._registry.list_routes():
            params = match_path_template(route.path, path)
            if params is None:
                continue
            structural_match = route
            structural_params = params
            if method is None or method.upper() in route.methods:
                return RouteMatch(
                    path=path,
                    template=route.path,
                    method=method.upper() if method else None,
                    params=params,
                    route=route,
                )
        if structural_match is not None:
            raise InvalidMethodError(f"{method.upper() if method else method} not allowed for {structural_match.path}")
        raise RouteNotFoundError(path)

    def resolve(self, path: str, method: Optional[str] = None) -> RoutingResult:
        try:
            found = self.match(path, method)
        except InvalidMethodError:
            return RoutingResult(
                matched=False,
                path=path,
                method=method.upper() if method else None,
                route=None,
                reason="method_not_allowed",
            )
        except RouteNotFoundError:
            return RoutingResult(
                matched=False,
                path=path,
                method=method.upper() if method else None,
                route=None,
                reason="not_found",
            )
        return RoutingResult(
            matched=True,
            path=path,
            method=found.method,
            route=found.route,
            params=found.params,
            reason="matched",
        )

    def fallback(self, path: str, method: Optional[str] = None, *, reason: str = "not_found") -> RoutingResult:
        if self._fallback_handler is not None:
            return self._fallback_handler(path, method)
        return RoutingResult(
            matched=False,
            path=path,
            method=method.upper() if method else None,
            route=None,
            reason=reason,
        )

    def route(
        self,
        path: str,
        method: Optional[str] = None,
        payload: Optional[dict] = None,
        *,
        headers: Optional[dict] = None,
        query_params: Optional[dict] = None,
    ) -> RoutingResult:
        context: Optional[MiddlewareContext] = None
        if self._middleware_pipeline is not None:
            context = MiddlewareContext(path=path, method=method.upper() if method else "", payload=payload or {})
            self._middleware_pipeline.execute_before(context)
            if context.short_circuited:
                result = RoutingResult(
                    matched=False,
                    path=path,
                    method=context.method,
                    route=None,
                    reason="short_circuited",
                    middleware_response=context.response,
                )
                self._middleware_pipeline.execute_after(context)
                return result

        result = self.resolve(path, method)
        if not result.matched:
            result = self.fallback(path, method, reason=result.reason)
        elif self._validation_engine is not None:
            try:
                rule = self._validation_engine.get_rule(result.route.path)
            except UnknownValidationRuleError:
                rule = None
            if rule is not None:
                validation = self._validation_engine.validate_request(
                    rule,
                    headers=headers,
                    params=query_params,
                    path_params=result.params,
                    body=payload,
                )
                if not validation.valid:
                    result = RoutingResult(
                        matched=False,
                        path=path,
                        method=result.method,
                        route=result.route,
                        params=result.params,
                        reason="validation_failed",
                        validation_errors=tuple(validation.errors),
                    )

        if context is not None:
            self._middleware_pipeline.execute_after(context)
        return result


_request_router = RequestRouter(
    get_route_registry(),
    middleware_pipeline=get_middleware_pipeline(),
    validation_engine=get_validation_engine(),
)


def get_request_router() -> RequestRouter:
    return _request_router


# Must be included in the app before route_registry.router, whose
# "/gateway/routes/{route:path}" catch-all would otherwise swallow
# "/gateway/routes/match" and "/gateway/routes/resolve" first.
router = APIRouter(prefix="/gateway", tags=["gateway-routing"])


@router.post("/route")
def route_request_endpoint(
    payload: dict = Body(default={}),
    request_router: RequestRouter = Depends(get_request_router),
) -> dict:
    result = request_router.route(
        payload.get("path", ""),
        payload.get("method"),
        payload.get("payload"),
        headers=payload.get("headers"),
        query_params=payload.get("params"),
    )
    return result.to_dict()


@router.get("/routes/match")
def match_route_endpoint(
    path: str = Query(...),
    method: Optional[str] = Query(default=None),
    request_router: RequestRouter = Depends(get_request_router),
) -> dict:
    try:
        found = request_router.match(path, method)
    except RouteNotFoundError:
        raise HTTPException(status_code=404, detail="no route matches path")
    except InvalidMethodError as exc:
        raise HTTPException(status_code=405, detail=str(exc))
    return found.to_dict()


@router.get("/routes/resolve")
def resolve_route_endpoint(
    path: str = Query(...),
    method: Optional[str] = Query(default=None),
    request_router: RequestRouter = Depends(get_request_router),
) -> dict:
    result = request_router.resolve(path, method)
    return result.to_dict()
