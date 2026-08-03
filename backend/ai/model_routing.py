from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .model_registry import ModelRegistry, get_model_registry


class RouteAlreadyRegisteredError(ValueError):
    pass


class UnknownRouteError(KeyError):
    pass


class InvalidStrategyError(ValueError):
    pass


class NoAvailableModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoutingRule:
    """A registered routing policy for how to pick a model for a logical route."""

    route_name: str
    strategy: str
    candidates: tuple
    capability: Optional[str]
    fallback: Optional[str]

    def to_dict(self) -> dict:
        return {
            "route_name": self.route_name,
            "strategy": self.strategy,
            "candidates": list(self.candidates),
            "capability": self.capability,
            "fallback": self.fallback,
        }


@dataclass(frozen=True)
class RoutingDecision:
    """The outcome of resolving a route to a concrete model."""

    route_name: str
    selected_model: str
    strategy: str
    is_fallback: bool
    reason: str
    decided_at: datetime

    def to_dict(self) -> dict:
        return {
            "route_name": self.route_name,
            "selected_model": self.selected_model,
            "strategy": self.strategy,
            "is_fallback": self.is_fallback,
            "reason": self.reason,
            "decided_at": self.decided_at.isoformat(),
        }


_STRATEGIES = ("priority", "capability", "latency", "weighted")


class ModelRoutingEngine:
    """Selects the best available model for a route according to a routing strategy."""

    def __init__(self) -> None:
        self._rules: dict = {}
        self._stats: dict = {}
        self._lock = Lock()

    def register_route(
        self,
        route_name: str,
        strategy: str,
        *,
        candidates: Optional[list] = None,
        capability: Optional[str] = None,
        fallback: Optional[str] = None,
    ) -> RoutingRule:
        if not route_name:
            raise ValueError("route_name is required")
        if strategy not in _STRATEGIES:
            raise InvalidStrategyError(f"unsupported routing strategy '{strategy}'")
        if strategy == "capability":
            if not capability:
                raise ValueError("'capability' strategy requires a capability")
        elif not candidates:
            raise ValueError(f"'{strategy}' strategy requires at least one candidate model")

        with self._lock:
            if route_name in self._rules:
                raise RouteAlreadyRegisteredError(f"route '{route_name}' is already registered")
            rule = RoutingRule(
                route_name=route_name,
                strategy=strategy,
                candidates=tuple(candidates or ()),
                capability=capability,
                fallback=fallback,
            )
            self._rules[route_name] = rule
            self._stats[route_name] = {"total": 0, "fallback_count": 0, "selections": {}}
        return rule

    def _candidate_models(
        self,
        rule: RoutingRule,
        registry: ModelRegistry,
        *,
        random_value: Optional[float],
    ) -> list:
        if rule.strategy == "priority":
            return [name for name in rule.candidates if registry.is_registered(name)]

        if rule.strategy == "capability":
            return [model.name for model in registry.list_models(capability=rule.capability)]

        if rule.strategy == "latency":
            models = [registry.get(name) for name in rule.candidates if registry.is_registered(name)]
            models.sort(key=lambda model: model.metadata.latency_ms)
            return [model.name for model in models]

        if rule.strategy == "weighted":
            weighted = [
                (name, registry.get(name).metadata.weight)
                for name in rule.candidates
                if registry.is_registered(name)
            ]
            if not weighted:
                return []
            total_weight = sum(weight for _, weight in weighted)
            by_weight_desc = [name for name, _ in sorted(weighted, key=lambda pair: pair[1], reverse=True)]
            if total_weight <= 0:
                return by_weight_desc
            pick = (random_value if random_value is not None else random.random()) * total_weight
            cumulative = 0.0
            for name, weight in weighted:
                cumulative += weight
                if pick <= cumulative:
                    return [name] + [other for other in by_weight_desc if other != name]
            return by_weight_desc

        return []

    def select(
        self,
        route_name: str,
        *,
        registry: ModelRegistry,
        random_value: Optional[float] = None,
    ) -> RoutingDecision:
        with self._lock:
            rule = self._rules.get(route_name)
            if rule is None:
                raise UnknownRouteError(route_name)

        pool = self._candidate_models(rule, registry, random_value=random_value)
        chosen = pool[0] if pool else None
        is_fallback = False
        if chosen is not None:
            reason = f"selected via '{rule.strategy}' strategy"
        else:
            chosen = self._resolve_fallback(rule, registry)
            is_fallback = True
            reason = "primary candidates unavailable; used fallback"

        decision = RoutingDecision(
            route_name=route_name,
            selected_model=chosen,
            strategy=rule.strategy,
            is_fallback=is_fallback,
            reason=reason,
            decided_at=datetime.now(timezone.utc),
        )
        with self._lock:
            stats = self._stats[route_name]
            stats["total"] += 1
            if is_fallback:
                stats["fallback_count"] += 1
            stats["selections"][chosen] = stats["selections"].get(chosen, 0) + 1
        return decision

    @staticmethod
    def _resolve_fallback(rule: RoutingRule, registry: ModelRegistry) -> str:
        if rule.fallback and registry.is_registered(rule.fallback):
            return rule.fallback
        raise NoAvailableModelError(f"no available model for route '{rule.route_name}'")

    def fallback(self, route_name: str, *, registry: ModelRegistry) -> str:
        with self._lock:
            rule = self._rules.get(route_name)
            if rule is None:
                raise UnknownRouteError(route_name)
        return self._resolve_fallback(rule, registry)

    def route_stats(self, route_name: Optional[str] = None) -> dict:
        with self._lock:
            if route_name is not None:
                stats = self._stats.get(route_name)
                if stats is None:
                    raise UnknownRouteError(route_name)
                return dict(stats)
            return {name: dict(stats) for name, stats in self._stats.items()}

    def list_routes(self) -> list:
        with self._lock:
            return sorted(self._rules.values(), key=lambda rule: rule.route_name)


_model_routing_engine = ModelRoutingEngine()


def get_model_routing_engine() -> ModelRoutingEngine:
    return _model_routing_engine


router = APIRouter(prefix="/ai/routing", tags=["model-routing"])


@router.post("", status_code=201)
def register_route_endpoint(
    payload: dict = Body(default={}),
    engine: ModelRoutingEngine = Depends(get_model_routing_engine),
) -> dict:
    try:
        rule = engine.register_route(
            payload.get("route_name", ""),
            payload.get("strategy", ""),
            candidates=payload.get("candidates"),
            capability=payload.get("capability"),
            fallback=payload.get("fallback"),
        )
    except RouteAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (InvalidStrategyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return rule.to_dict()


@router.get("")
def list_routes_endpoint(
    engine: ModelRoutingEngine = Depends(get_model_routing_engine),
) -> list:
    return [rule.to_dict() for rule in engine.list_routes()]


@router.post("/select")
def select_route_endpoint(
    payload: dict = Body(default={}),
    engine: ModelRoutingEngine = Depends(get_model_routing_engine),
    registry: ModelRegistry = Depends(get_model_registry),
) -> dict:
    try:
        decision = engine.select(
            payload.get("route_name", ""),
            registry=registry,
            random_value=payload.get("random_value"),
        )
    except UnknownRouteError:
        raise HTTPException(status_code=404, detail="unknown route")
    except NoAvailableModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return decision.to_dict()


@router.get("/stats")
def route_stats_endpoint(
    route: Optional[str] = Query(default=None),
    engine: ModelRoutingEngine = Depends(get_model_routing_engine),
) -> dict:
    try:
        return engine.route_stats(route)
    except UnknownRouteError:
        raise HTTPException(status_code=404, detail="unknown route")
