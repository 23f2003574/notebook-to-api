from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class RouteAlreadyRegisteredError(ValueError):
    pass


class RouteNotFoundError(KeyError):
    pass


class InvalidMethodError(ValueError):
    pass


def match_path_template(template: str, path: str) -> Optional[dict]:
    """Match a concrete path against a route template, extracting `{name}` segments.

    Returns the extracted parameters as a dict, or None if the path does not
    match the template's shape.
    """

    template_segments = [segment for segment in template.split("/") if segment != ""]
    path_segments = [segment for segment in path.split("/") if segment != ""]
    if len(template_segments) != len(path_segments):
        return None
    params: dict = {}
    for template_segment, path_segment in zip(template_segments, path_segments):
        if template_segment.startswith("{") and template_segment.endswith("}"):
            params[template_segment[1:-1]] = path_segment
        elif template_segment != path_segment:
            return None
    return params


@dataclass(frozen=True)
class RouteMetadata:
    """Descriptive information attached to a registered route."""

    description: str = ""
    owner: str = ""
    tags: tuple = ()
    deprecated: bool = False

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "owner": self.owner,
            "tags": list(self.tags),
            "deprecated": self.deprecated,
        }

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "RouteMetadata":
        payload = payload or {}
        return cls(
            description=payload.get("description", ""),
            owner=payload.get("owner", ""),
            tags=tuple(payload.get("tags", ())),
            deprecated=bool(payload.get("deprecated", False)),
        )


@dataclass(frozen=True)
class Route:
    """A single registered route and the methods it accepts."""

    path: str
    methods: tuple
    metadata: RouteMetadata
    registered_at: datetime

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "methods": list(self.methods),
            "metadata": self.metadata.to_dict(),
            "registered_at": self.registered_at.isoformat(),
        }


class RouteRegistry:
    """Tracks registered routes, their allowed methods, and discovery metadata."""

    def __init__(self) -> None:
        self._routes: dict = {}
        self._tag_index: dict = {}
        self._lock = Lock()

    def register(
        self,
        path: str,
        methods: Iterable[str],
        metadata: Optional[RouteMetadata] = None,
    ) -> Route:
        if not path:
            raise ValueError("path is required")
        path = path if path.startswith("/") else f"/{path}"
        normalized_methods = tuple(sorted({method.upper() for method in methods}))
        if not normalized_methods:
            raise ValueError("at least one method is required")
        invalid_methods = [method for method in normalized_methods if method not in ALLOWED_METHODS]
        if invalid_methods:
            raise InvalidMethodError(f"unsupported HTTP method(s): {', '.join(invalid_methods)}")
        metadata = metadata or RouteMetadata()
        with self._lock:
            if path in self._routes:
                raise RouteAlreadyRegisteredError(f"{path} is already registered")
            route = Route(
                path=path,
                methods=normalized_methods,
                metadata=metadata,
                registered_at=datetime.now(timezone.utc),
            )
            self._routes[path] = route
            for tag in metadata.tags:
                self._tag_index.setdefault(tag, set()).add(path)
        return route

    def unregister(self, path: str) -> None:
        path = path if path.startswith("/") else f"/{path}"
        with self._lock:
            route = self._routes.pop(path, None)
            if route is None:
                raise RouteNotFoundError(path)
            for tag in route.metadata.tags:
                paths = self._tag_index.get(tag)
                if paths is not None:
                    paths.discard(path)
                    if not paths:
                        del self._tag_index[tag]

    def resolve(self, path: str, method: Optional[str] = None) -> Route:
        path = path if path.startswith("/") else f"/{path}"
        with self._lock:
            route = self._routes.get(path)
            if route is None:
                raise RouteNotFoundError(path)
            if method is not None and method.upper() not in route.methods:
                raise InvalidMethodError(f"{method.upper()} not allowed for {path}")
            return route

    def list_routes(self, tag: Optional[str] = None) -> list:
        with self._lock:
            if tag is not None:
                paths = sorted(self._tag_index.get(tag, set()))
            else:
                paths = sorted(self._routes)
            return [self._routes[path] for path in paths if path in self._routes]


_route_registry = RouteRegistry()


def get_route_registry() -> RouteRegistry:
    return _route_registry


router = APIRouter(prefix="/gateway/routes", tags=["gateway-routes"])


@router.post("", status_code=201)
def register_route_endpoint(
    payload: dict = Body(default={}),
    registry: RouteRegistry = Depends(get_route_registry),
) -> dict:
    try:
        route = registry.register(
            payload.get("path", ""),
            payload.get("methods", []),
            RouteMetadata.from_dict(payload.get("metadata")),
        )
    except RouteAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, InvalidMethodError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return route.to_dict()


@router.get("")
def list_routes_endpoint(
    tag: Optional[str] = Query(default=None),
    registry: RouteRegistry = Depends(get_route_registry),
) -> list:
    return [route.to_dict() for route in registry.list_routes(tag=tag)]


@router.get("/{route:path}")
def get_route_endpoint(
    route: str,
    registry: RouteRegistry = Depends(get_route_registry),
) -> dict:
    try:
        found = registry.resolve(route)
    except RouteNotFoundError:
        raise HTTPException(status_code=404, detail="unknown route")
    return found.to_dict()


@router.delete("/{route:path}", status_code=204)
def remove_route_endpoint(
    route: str,
    registry: RouteRegistry = Depends(get_route_registry),
) -> None:
    try:
        registry.unregister(route)
    except RouteNotFoundError:
        raise HTTPException(status_code=404, detail="unknown route")
