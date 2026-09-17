from __future__ import annotations

import random
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .traffic_policy import TrafficDeniedError, TrafficPolicyEngine

STRATEGIES = frozenset({"round_robin", "least_connections", "weighted_round_robin", "random"})


class InvalidStrategyError(ValueError):
    pass


class BackendAlreadyRegisteredError(ValueError):
    pass


class UnknownBackendError(KeyError):
    pass


class NoHealthyBackendError(RuntimeError):
    pass


@dataclass
class BackendNode:
    """A single backend service the load balancer can route traffic to."""

    name: str
    address: str
    weight: int = 1
    healthy: bool = True
    active_connections: int = 0
    selections: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "address": self.address,
            "weight": self.weight,
            "healthy": self.healthy,
            "active_connections": self.active_connections,
            "selections": self.selections,
        }


@dataclass(frozen=True)
class LoadBalancerState:
    """A point-in-time snapshot of the load balancer's backends and traffic distribution."""

    strategy: str
    total_backends: int
    healthy_backends: int
    total_selections: int
    backends: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "total_backends": self.total_backends,
            "healthy_backends": self.healthy_backends,
            "total_selections": self.total_selections,
            "backends": list(self.backends),
        }


class LoadBalancer:
    """Distributes requests across registered backends using a configurable strategy."""

    def __init__(
        self,
        strategy: str = "round_robin",
        *,
        random_source: Optional[random.Random] = None,
        policy_engine: Optional[TrafficPolicyEngine] = None,
    ) -> None:
        if strategy not in STRATEGIES:
            raise InvalidStrategyError(f"unsupported strategy: {strategy}")
        self._strategy = strategy
        self._backends: dict = {}
        self._order: list = []
        self._rr_index = 0
        self._wrr_current_weight: dict = {}
        self._total_selections = 0
        self._lock = Lock()
        self._random = random_source or random.Random()
        self._policy_engine = policy_engine

    def register_backend(self, name: str, address: str, *, weight: int = 1) -> BackendNode:
        if not name:
            raise ValueError("name is required")
        if not address:
            raise ValueError("address is required")
        if weight <= 0:
            raise ValueError("weight must be positive")
        with self._lock:
            if name in self._backends:
                raise BackendAlreadyRegisteredError(f"{name} is already registered")
            node = BackendNode(name=name, address=address, weight=weight)
            self._backends[name] = node
            self._order.append(name)
            self._wrr_current_weight[name] = 0
        return node

    def _require(self, name: str) -> BackendNode:
        node = self._backends.get(name)
        if node is None:
            raise UnknownBackendError(name)
        return node

    def mark_unhealthy(self, name: str, healthy: bool = False) -> BackendNode:
        with self._lock:
            node = self._require(name)
            node.healthy = healthy
            return node

    def release_backend(self, name: str) -> BackendNode:
        with self._lock:
            node = self._require(name)
            node.active_connections = max(node.active_connections - 1, 0)
            return node

    def list_backends(self) -> list:
        with self._lock:
            return [self._backends[name] for name in self._order]

    def _healthy_backends(self) -> list:
        return [self._backends[name] for name in self._order if self._backends[name].healthy]

    def _select_round_robin(self, healthy: list) -> BackendNode:
        total = len(self._order)
        for offset in range(total):
            index = (self._rr_index + offset) % total
            node = self._backends[self._order[index]]
            if node.healthy:
                self._rr_index = (index + 1) % total
                return node
        raise NoHealthyBackendError("no healthy backend available")

    def _select_least_connections(self, healthy: list) -> BackendNode:
        return min(healthy, key=lambda node: (node.active_connections, self._order.index(node.name)))

    def _select_weighted_round_robin(self, healthy: list) -> BackendNode:
        total_weight = sum(node.weight for node in healthy)
        for node in healthy:
            self._wrr_current_weight[node.name] += node.weight
        chosen = max(healthy, key=lambda node: (self._wrr_current_weight[node.name], -self._order.index(node.name)))
        self._wrr_current_weight[chosen.name] -= total_weight
        return chosen

    def _select_random(self, healthy: list) -> BackendNode:
        return self._random.choice(healthy)

    def select(self, context: Optional[dict] = None) -> BackendNode:
        with self._lock:
            if self._policy_engine is not None and context is not None:
                decision = self._policy_engine.evaluate(context)
                if decision is not None and decision.action == "deny":
                    raise TrafficDeniedError(decision.reason)
                if decision is not None and decision.action == "route":
                    node = self._backends.get(decision.metadata.get("target"))
                    if node is not None and node.healthy:
                        node.selections += 1
                        node.active_connections += 1
                        self._total_selections += 1
                        return node

            healthy = self._healthy_backends()
            if not healthy:
                raise NoHealthyBackendError("no healthy backend available")
            if self._strategy == "round_robin":
                node = self._select_round_robin(healthy)
            elif self._strategy == "least_connections":
                node = self._select_least_connections(healthy)
            elif self._strategy == "weighted_round_robin":
                node = self._select_weighted_round_robin(healthy)
            else:
                node = self._select_random(healthy)
            node.selections += 1
            node.active_connections += 1
            self._total_selections += 1
            return node

    def rebalance(self) -> LoadBalancerState:
        with self._lock:
            backends = tuple(self._backends[name].to_dict() for name in self._order)
            healthy_count = sum(1 for name in self._order if self._backends[name].healthy)
            return LoadBalancerState(
                strategy=self._strategy,
                total_backends=len(self._order),
                healthy_backends=healthy_count,
                total_selections=self._total_selections,
                backends=backends,
            )


_load_balancer = LoadBalancer()


def get_load_balancer() -> LoadBalancer:
    return _load_balancer


router = APIRouter(prefix="/gateway", tags=["gateway-load-balancer"])


@router.post("/backends", status_code=201)
def register_backend_endpoint(
    payload: dict = Body(default={}),
    load_balancer: LoadBalancer = Depends(get_load_balancer),
) -> dict:
    try:
        node = load_balancer.register_backend(
            payload.get("name", ""),
            payload.get("address", ""),
            weight=payload.get("weight", 1),
        )
    except BackendAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return node.to_dict()


@router.get("/backends")
def list_backends_endpoint(load_balancer: LoadBalancer = Depends(get_load_balancer)) -> list:
    return [node.to_dict() for node in load_balancer.list_backends()]


@router.post("/backends/{backend}/health")
def update_backend_health_endpoint(
    backend: str,
    payload: dict = Body(default={}),
    load_balancer: LoadBalancer = Depends(get_load_balancer),
) -> dict:
    try:
        node = load_balancer.mark_unhealthy(backend, healthy=payload.get("healthy", False))
    except UnknownBackendError:
        raise HTTPException(status_code=404, detail="unknown backend")
    return node.to_dict()


@router.get("/load-balancer/status")
def load_balancer_status_endpoint(load_balancer: LoadBalancer = Depends(get_load_balancer)) -> dict:
    return load_balancer.rebalance().to_dict()
