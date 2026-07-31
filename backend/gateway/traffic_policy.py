from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

PolicyHandler = Callable[[dict], "PolicyResult"]


class PolicyAlreadyRegisteredError(ValueError):
    pass


class UnknownPolicyError(KeyError):
    pass


class TrafficDeniedError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class PolicyResult:
    """The outcome of evaluating a single traffic policy against a request context."""

    policy: str
    matched: bool
    action: str
    reason: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "policy": self.policy,
            "matched": self.matched,
            "action": self.action,
            "reason": self.reason,
            "metadata": self.metadata,
        }


@dataclass
class TrafficPolicy:
    """A registered policy: its evaluation logic plus registry metadata."""

    name: str
    policy_type: str
    handler: PolicyHandler
    priority: int = 0
    enabled: bool = True
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "policy_type": self.policy_type,
            "priority": self.priority,
            "enabled": self.enabled,
            "config": self.config,
        }


class TrafficPolicyEngine:
    """Registers traffic policies and evaluates them in priority order."""

    def __init__(self) -> None:
        self._policies: dict = {}
        self._lock = Lock()

    def register_policy(
        self,
        name: str,
        handler: PolicyHandler,
        *,
        priority: int = 0,
        enabled: bool = True,
        policy_type: str = "custom",
        config: Optional[dict] = None,
    ) -> TrafficPolicy:
        if not name:
            raise ValueError("name is required")
        if handler is None:
            raise ValueError("handler is required")
        with self._lock:
            if name in self._policies:
                raise PolicyAlreadyRegisteredError(f"{name} is already registered")
            policy = TrafficPolicy(
                name=name,
                policy_type=policy_type,
                handler=handler,
                priority=priority,
                enabled=enabled,
                config=config or {},
            )
            self._policies[name] = policy
        return policy

    def _require(self, name: str) -> TrafficPolicy:
        policy = self._policies.get(name)
        if policy is None:
            raise UnknownPolicyError(name)
        return policy

    def enable(self, name: str) -> TrafficPolicy:
        with self._lock:
            policy = self._require(name)
            policy.enabled = True
            return policy

    def disable(self, name: str) -> TrafficPolicy:
        with self._lock:
            policy = self._require(name)
            policy.enabled = False
            return policy

    def list_policies(self) -> list:
        with self._lock:
            return sorted(self._policies.values(), key=lambda policy: (policy.priority, policy.name))

    def evaluate(self, context: dict, *, name: Optional[str] = None) -> Optional[PolicyResult]:
        if name is not None:
            policy = self._require(name)
            if not policy.enabled:
                return PolicyResult(policy=name, matched=False, action="skip", reason="policy disabled")
            return policy.handler(context)

        for policy in self.list_policies():
            if not policy.enabled:
                continue
            result = policy.handler(context)
            if result.matched:
                return result
        return None


def _build_geo_routing(name: str, config: dict) -> PolicyHandler:
    field_name = config.get("field", "country")
    routes = config.get("routes", {})
    default_target = config.get("default")

    def handler(context: dict) -> PolicyResult:
        country = context.get(field_name)
        target = routes.get(country, default_target)
        if target is None:
            return PolicyResult(policy=name, matched=False, action="skip", reason="no route configured")
        return PolicyResult(
            policy=name,
            matched=True,
            action="route",
            reason=f"geo routed via {field_name}={country}",
            metadata={"target": target, field_name: country},
        )

    return handler


def _build_maintenance_mode(name: str, config: dict) -> PolicyHandler:
    allowed_paths = set(config.get("allowed_paths", []))
    message = config.get("message", "service under maintenance")

    def handler(context: dict) -> PolicyResult:
        if not config.get("active", False):
            return PolicyResult(policy=name, matched=False, action="skip", reason="maintenance not active")
        if context.get("path") in allowed_paths:
            return PolicyResult(policy=name, matched=False, action="skip", reason="path allow-listed during maintenance")
        return PolicyResult(policy=name, matched=True, action="deny", reason=message)

    return handler


def _build_canary_routing(name: str, config: dict) -> PolicyHandler:
    percentage = config.get("percentage", 0)
    canary_target = config.get("canary_target")
    stable_target = config.get("stable_target")
    key_field = config.get("key", "client_id")

    def handler(context: dict) -> PolicyResult:
        if canary_target is None:
            return PolicyResult(policy=name, matched=False, action="skip", reason="no canary target configured")
        key = str(context.get(key_field, ""))
        bucket = zlib.crc32(key.encode("utf-8")) % 100
        if bucket < percentage:
            return PolicyResult(
                policy=name,
                matched=True,
                action="route",
                reason=f"canary bucket {bucket} < {percentage}",
                metadata={"target": canary_target, "bucket": bucket},
            )
        if stable_target is None:
            return PolicyResult(policy=name, matched=False, action="skip", reason="stable bucket, no override")
        return PolicyResult(
            policy=name,
            matched=True,
            action="route",
            reason=f"stable bucket {bucket}",
            metadata={"target": stable_target, "bucket": bucket},
        )

    return handler


def _build_traffic_shaping(name: str, config: dict) -> PolicyHandler:
    affected_paths = tuple(config.get("affected_paths", []))
    delay_ms = config.get("delay_ms", 0)

    def handler(context: dict) -> PolicyResult:
        path = context.get("path", "")
        if not any(path.startswith(prefix) for prefix in affected_paths):
            return PolicyResult(policy=name, matched=False, action="skip", reason="path not shaped")
        return PolicyResult(
            policy=name,
            matched=True,
            action="shape",
            reason=f"applying {delay_ms}ms delay",
            metadata={"delay_ms": delay_ms},
        )

    return handler


BUILTIN_POLICY_FACTORIES = {
    "geo_routing": _build_geo_routing,
    "maintenance_mode": _build_maintenance_mode,
    "canary_routing": _build_canary_routing,
    "traffic_shaping": _build_traffic_shaping,
}


_policy_engine = TrafficPolicyEngine()


def get_policy_engine() -> TrafficPolicyEngine:
    return _policy_engine


router = APIRouter(prefix="/gateway", tags=["gateway-traffic-policy"])


@router.post("/policies", status_code=201)
def register_policy_endpoint(
    payload: dict = Body(default={}),
    engine: TrafficPolicyEngine = Depends(get_policy_engine),
) -> dict:
    policy_type = payload.get("type", "")
    factory = BUILTIN_POLICY_FACTORIES.get(policy_type)
    if factory is None:
        raise HTTPException(status_code=422, detail=f"unknown policy type: {policy_type}")
    name = payload.get("name") or policy_type
    config = payload.get("config") or {}
    handler = factory(name, config)
    try:
        policy = engine.register_policy(
            name,
            handler,
            priority=payload.get("priority", 0),
            enabled=payload.get("enabled", True),
            policy_type=policy_type,
            config=config,
        )
    except PolicyAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return policy.to_dict()


@router.get("/policies")
def list_policies_endpoint(engine: TrafficPolicyEngine = Depends(get_policy_engine)) -> list:
    return [policy.to_dict() for policy in engine.list_policies()]


@router.post("/policies/{policy}/evaluate")
def evaluate_policy_endpoint(
    policy: str,
    payload: dict = Body(default={}),
    engine: TrafficPolicyEngine = Depends(get_policy_engine),
) -> dict:
    try:
        result = engine.evaluate(payload.get("context", {}), name=policy)
    except UnknownPolicyError:
        raise HTTPException(status_code=404, detail="unknown policy")
    return result.to_dict()
