from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .artifact_manager import Artifact, ArtifactManager, ArtifactType, get_artifact_manager


class PolicyType(str, Enum):
    """The kinds of lifecycle actions a policy can trigger."""

    RETENTION = "retention"
    ARCHIVE = "archive"
    EXPIRATION = "expiration"
    TRANSITION = "transition"


@dataclass(frozen=True)
class RetentionRule:
    """The matching criteria and age threshold that trigger a policy."""

    max_age_seconds: int
    namespace: Optional[str] = None
    artifact_type: Optional[ArtifactType] = None
    tier: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "max_age_seconds": self.max_age_seconds,
            "namespace": self.namespace,
            "artifact_type": self.artifact_type.value if self.artifact_type else None,
            "tier": self.tier,
        }

    def matches(self, artifact: Artifact) -> bool:
        if self.namespace is not None and artifact.namespace != self.namespace:
            return False
        if self.artifact_type is not None and artifact.artifact_type != self.artifact_type:
            return False
        return True

    def is_triggered(self, artifact: Artifact, *, now: datetime) -> bool:
        age_seconds = (now - artifact.created_at).total_seconds()
        return self.matches(artifact) and age_seconds >= self.max_age_seconds


@dataclass
class LifecyclePolicy:
    """A named rule that governs how matching artifacts age through storage."""

    policy_id: str
    name: str
    policy_type: PolicyType
    rule: RetentionRule
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "policy_type": self.policy_type.value,
            "rule": self.rule.to_dict(),
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class PolicyExecution:
    """A record of a single policy action taken against an artifact."""

    execution_id: str
    policy_id: str
    artifact_id: str
    action: str
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "policy_id": self.policy_id,
            "artifact_id": self.artifact_id,
            "action": self.action,
            "executed_at": self.executed_at.isoformat(),
        }


class LifecyclePolicyManager:
    """Evaluates and applies rule-based retention, archive, and expiration policies."""

    def __init__(self, *, artifact_manager: ArtifactManager) -> None:
        self._artifact_manager = artifact_manager
        self._policies: dict = {}
        self._history: list = []
        self._lock = Lock()

    def create_policy(
        self,
        name: str,
        policy_type: PolicyType,
        rule: RetentionRule,
        *,
        enabled: bool = True,
    ) -> LifecyclePolicy:
        if not name:
            raise ValueError("name must be non-empty")
        if rule.max_age_seconds < 0:
            raise ValueError("max_age_seconds must be >= 0")

        policy = LifecyclePolicy(
            policy_id=uuid.uuid4().hex,
            name=name,
            policy_type=policy_type,
            rule=rule,
            enabled=enabled,
        )
        with self._lock:
            self._policies[policy.policy_id] = policy
        return policy

    def list_policies(self) -> list:
        with self._lock:
            return sorted(self._policies.values(), key=lambda policy: policy.policy_id)

    def evaluate(self, artifact_id: str) -> list:
        artifact = self._artifact_manager.fetch(artifact_id)
        if artifact is None:
            raise KeyError(artifact_id)

        now = datetime.now(timezone.utc)
        with self._lock:
            policies = list(self._policies.values())
        return [
            policy
            for policy in policies
            if policy.enabled and policy.rule.is_triggered(artifact, now=now)
        ]

    def expire(self, artifact_id: str, *, policy_id: Optional[str] = None) -> PolicyExecution:
        artifact = self._artifact_manager.fetch(artifact_id)
        if artifact is None:
            raise KeyError(artifact_id)

        self._artifact_manager.delete(artifact_id)
        return self._record(policy_id or "manual", artifact_id, "expired")

    def apply(self) -> list:
        executions = []
        for artifact in self._artifact_manager.list_artifacts():
            try:
                triggered = self.evaluate(artifact.artifact_id)
            except KeyError:
                continue

            for policy in triggered:
                if policy.policy_type == PolicyType.EXPIRATION:
                    executions.append(self.expire(artifact.artifact_id, policy_id=policy.policy_id))
                    break
                if policy.policy_type in (PolicyType.ARCHIVE, PolicyType.TRANSITION):
                    tier = policy.rule.tier or policy.policy_type.value
                    self._artifact_manager.set_tier(artifact.artifact_id, tier)
                    executions.append(self._record(policy.policy_id, artifact.artifact_id, policy.policy_type.value))
                else:
                    executions.append(self._record(policy.policy_id, artifact.artifact_id, "retained"))
        return executions

    def list_history(self) -> list:
        with self._lock:
            return list(self._history)

    def _record(self, policy_id: str, artifact_id: str, action: str) -> PolicyExecution:
        execution = PolicyExecution(
            execution_id=uuid.uuid4().hex,
            policy_id=policy_id,
            artifact_id=artifact_id,
            action=action,
        )
        with self._lock:
            self._history.append(execution)
        return execution


_lifecycle_policy_manager = LifecyclePolicyManager(artifact_manager=get_artifact_manager())


def get_lifecycle_policy_manager() -> LifecyclePolicyManager:
    return _lifecycle_policy_manager


router = APIRouter(prefix="/storage/lifecycle", tags=["lifecycle-policy"])


@router.post("")
def create_policy_endpoint(
    name: str,
    policy_type: PolicyType,
    max_age_seconds: int,
    namespace: Optional[str] = None,
    artifact_type: Optional[ArtifactType] = None,
    tier: Optional[str] = None,
    enabled: bool = True,
    manager: LifecyclePolicyManager = Depends(get_lifecycle_policy_manager),
) -> dict:
    rule = RetentionRule(
        max_age_seconds=max_age_seconds,
        namespace=namespace,
        artifact_type=artifact_type,
        tier=tier,
    )
    try:
        policy = manager.create_policy(name, policy_type, rule, enabled=enabled)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return policy.to_dict()


@router.get("")
def list_policies_endpoint(
    manager: LifecyclePolicyManager = Depends(get_lifecycle_policy_manager),
) -> list:
    return [policy.to_dict() for policy in manager.list_policies()]


@router.post("/apply")
def apply_endpoint(
    manager: LifecyclePolicyManager = Depends(get_lifecycle_policy_manager),
) -> list:
    return [execution.to_dict() for execution in manager.apply()]


@router.get("/history")
def history_endpoint(
    manager: LifecyclePolicyManager = Depends(get_lifecycle_policy_manager),
) -> list:
    return [execution.to_dict() for execution in manager.list_history()]
