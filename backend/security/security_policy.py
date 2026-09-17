from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Optional

from fastapi import APIRouter, Body, HTTPException

from .permissions import PermissionEngine, get_permission_engine


def _evaluate_password_strength(context: dict) -> tuple:
    password = context.get("password", "")
    min_length = context.get("min_length", 12)
    if len(password) < min_length:
        return False, f"password must be at least {min_length} characters"
    if not any(char.isupper() for char in password):
        return False, "password must contain an uppercase letter"
    if not any(char.isdigit() for char in password):
        return False, "password must contain a digit"
    return True, None


def _evaluate_mfa_required(context: dict) -> tuple:
    if not context.get("mfa_enabled", False):
        return False, "multi-factor authentication is required"
    return True, None


def _evaluate_session_timeout(context: dict) -> tuple:
    idle_minutes = context.get("idle_minutes", 0)
    max_idle_minutes = context.get("max_idle_minutes", 30)
    if idle_minutes > max_idle_minutes:
        return False, f"session idle for {idle_minutes} minutes exceeds limit of {max_idle_minutes}"
    return True, None


def _evaluate_api_key_rotation(context: dict) -> tuple:
    age_days = context.get("age_days", 0)
    max_age_days = context.get("max_age_days", 90)
    if age_days > max_age_days:
        return False, f"API key age of {age_days} days exceeds rotation limit of {max_age_days}"
    return True, None


_BUILTIN_EVALUATORS: dict = {
    "Password Strength": _evaluate_password_strength,
    "MFA Required": _evaluate_mfa_required,
    "Session Timeout": _evaluate_session_timeout,
    "API Key Rotation": _evaluate_api_key_rotation,
}

BUILTIN_POLICIES = tuple(_BUILTIN_EVALUATORS)


class UnknownEvaluatorError(ValueError):
    pass


class PolicyAlreadyExistsError(ValueError):
    pass


class UnknownPolicyError(KeyError):
    pass


@dataclass(frozen=True)
class SecurityPolicy:
    """A registered, configurable instance of a built-in policy type."""

    name: str
    enabled: bool = True
    config: dict = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "config": dict(self.config or {}),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class PolicyResult:
    """The outcome of evaluating a policy against a context."""

    policy: str
    passed: bool
    message: Optional[str] = None
    overridden: bool = False
    evaluated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "policy": self.policy,
            "passed": self.passed,
            "message": self.message,
            "overridden": self.overridden,
            "evaluated_at": self.evaluated_at.isoformat() if self.evaluated_at else None,
        }


class SecurityPolicyEngine:
    """Registers and evaluates organization-wide security policies."""

    def __init__(self, permission_engine: Optional[PermissionEngine] = None) -> None:
        self._permission_engine = permission_engine or get_permission_engine()
        self._policies: dict[str, SecurityPolicy] = {}
        self._evaluators: dict[str, Callable] = dict(_BUILTIN_EVALUATORS)
        self._lock = Lock()

    def register_policy(
        self,
        name: str,
        *,
        enabled: bool = True,
        config: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> SecurityPolicy:
        if name not in self._evaluators:
            raise UnknownEvaluatorError(f"no evaluator registered for policy '{name}'")
        with self._lock:
            if name in self._policies:
                raise PolicyAlreadyExistsError(f"policy '{name}' already exists")
            policy = SecurityPolicy(
                name=name,
                enabled=enabled,
                config=dict(config or {}),
                created_at=timestamp or datetime.now(timezone.utc),
            )
            self._policies[name] = policy
        return policy

    def get_policy(self, name: str) -> SecurityPolicy:
        with self._lock:
            policy = self._policies.get(name)
        if policy is None:
            raise UnknownPolicyError(name)
        return policy

    def list_policies(self) -> list:
        with self._lock:
            return list(self._policies.values())

    def enable(self, name: str) -> SecurityPolicy:
        with self._lock:
            policy = self._policies.get(name)
            if policy is None:
                raise UnknownPolicyError(name)
            updated = replace(policy, enabled=True)
            self._policies[name] = updated
        return updated

    def disable(self, name: str) -> SecurityPolicy:
        with self._lock:
            policy = self._policies.get(name)
            if policy is None:
                raise UnknownPolicyError(name)
            updated = replace(policy, enabled=False)
            self._policies[name] = updated
        return updated

    def evaluate(
        self,
        name: str,
        context: Optional[dict] = None,
        *,
        user_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> PolicyResult:
        policy = self.get_policy(name)
        now = timestamp or datetime.now(timezone.utc)

        if user_id is not None and self._permission_engine.has_admin_override(
            user_id, f"policy:{name}"
        ):
            return PolicyResult(
                policy=name,
                passed=True,
                message="overridden by admin permission",
                overridden=True,
                evaluated_at=now,
            )

        if not policy.enabled:
            return PolicyResult(policy=name, passed=True, message="policy disabled", evaluated_at=now)

        merged_context = dict(policy.config)
        merged_context.update(context or {})
        passed, message = self._evaluators[name](merged_context)
        return PolicyResult(policy=name, passed=passed, message=message, evaluated_at=now)

    def evaluate_if_registered(
        self,
        name: str,
        context: Optional[dict] = None,
        *,
        user_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> Optional[PolicyResult]:
        try:
            self.get_policy(name)
        except UnknownPolicyError:
            return None
        return self.evaluate(name, context, user_id=user_id, timestamp=timestamp)


_security_policy_engine = SecurityPolicyEngine()


def get_security_policy_engine() -> SecurityPolicyEngine:
    return _security_policy_engine


router = APIRouter(prefix="/security", tags=["security-policy"])


@router.post("/policies")
def register_policy_endpoint(payload: dict = Body(default={})) -> dict:
    try:
        policy = get_security_policy_engine().register_policy(
            payload.get("name", ""),
            enabled=payload.get("enabled", True),
            config=payload.get("config"),
        )
    except UnknownEvaluatorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except PolicyAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return policy.to_dict()


@router.get("/policies")
def list_policies_endpoint() -> list:
    return [policy.to_dict() for policy in get_security_policy_engine().list_policies()]


@router.post("/policies/{policy}/evaluate")
def evaluate_policy_endpoint(policy: str, payload: dict = Body(default={})) -> dict:
    try:
        result = get_security_policy_engine().evaluate(
            policy, payload.get("context", {}), user_id=payload.get("user_id")
        )
    except UnknownPolicyError:
        raise HTTPException(status_code=404, detail="unknown policy")
    return result.to_dict()
