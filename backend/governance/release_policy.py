from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Optional

from fastapi import APIRouter, Body, HTTPException

from .artifact_promotion import ArtifactPromotionEngine, ENVIRONMENTS, get_artifact_promotion_engine
from .release_manager import Release, ReleaseManager, UnknownReleaseError, get_release_manager


def _new_id() -> str:
    return uuid.uuid4().hex


class PolicyAlreadyExistsError(ValueError):
    pass


class UnknownPolicyError(KeyError):
    pass


class NoEvaluationError(KeyError):
    pass


@dataclass(frozen=True)
class ReleasePolicy:
    """An immutable, registered named check that a release can be evaluated against."""

    policy_id: str
    name: str
    description: str = ""
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class PolicyResult:
    """The outcome of evaluating one policy against one release."""

    policy_id: str
    name: str
    passed: bool
    message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "passed": self.passed,
            "message": self.message,
        }


class ReleasePolicyEngine:
    """Registers and evaluates governance policies against releases."""

    def __init__(
        self,
        release_manager: Optional[ReleaseManager] = None,
        promotion_engine: Optional[ArtifactPromotionEngine] = None,
    ) -> None:
        self._release_manager = release_manager or get_release_manager()
        self._promotion_engine = promotion_engine or get_artifact_promotion_engine()
        self._policies: dict[str, ReleasePolicy] = {}
        self._checks: dict[str, Callable[[Release], bool]] = {}
        self._by_name: dict[str, str] = {}
        self._last_results: dict[str, list[PolicyResult]] = {}
        self._lock = Lock()
        self._register_builtin_policies()

    def _register_builtin_policies(self) -> None:
        self.register_policy(
            "Approval Required",
            lambda release: False,
            description="Requires an explicit manual sign-off override before publication.",
        )
        self.register_policy(
            "Artifact Verified",
            lambda release: all(
                self._promotion_engine.current_environment(
                    artifact["name"], artifact["version"]
                )
                == ENVIRONMENTS[-1]
                for artifact in release.artifacts
            ),
            description=f"Requires every release artifact to be promoted to '{ENVIRONMENTS[-1]}'.",
        )
        self.register_policy(
            "Release Notes Present",
            lambda release: release.notes_id is not None,
            description="Requires release notes to have been generated.",
        )
        self.register_policy(
            "Channel Assigned",
            lambda release: release.channel_id is not None,
            description="Requires the release to be assigned to a distribution channel.",
        )

    def register_policy(
        self,
        name: str,
        check: Callable[[Release], bool],
        *,
        description: str = "",
        timestamp: Optional[datetime] = None,
    ) -> ReleasePolicy:
        if not name:
            raise ValueError("policy name is required")
        if not callable(check):
            raise ValueError("check must be callable")

        policy = ReleasePolicy(
            policy_id=_new_id(),
            name=name,
            description=description,
            created_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            if name in self._by_name:
                raise PolicyAlreadyExistsError(f"policy '{name}' already exists")
            self._policies[policy.policy_id] = policy
            self._checks[policy.policy_id] = check
            self._by_name[name] = policy.policy_id
        return policy

    def remove_policy(self, name: str) -> None:
        with self._lock:
            policy_id = self._by_name.get(name)
            if policy_id is None:
                raise UnknownPolicyError(name)
            del self._by_name[name]
            del self._policies[policy_id]
            del self._checks[policy_id]

    def evaluate(
        self,
        release_id: str,
        *,
        overrides: Optional[dict] = None,
    ) -> list[PolicyResult]:
        release = self._release_manager.get(release_id)
        overrides = overrides or {}

        with self._lock:
            policies = list(self._policies.values())

        results = []
        for policy in policies:
            if policy.name in overrides:
                passed = bool(overrides[policy.name])
                message = "manual override"
            else:
                passed = bool(self._checks[policy.policy_id](release))
                message = None
            results.append(
                PolicyResult(
                    policy_id=policy.policy_id,
                    name=policy.name,
                    passed=passed,
                    message=message,
                )
            )

        with self._lock:
            self._last_results[release_id] = results
        self._release_manager.mark_policy_result(
            release_id, all(result.passed for result in results)
        )
        return results

    def summary(self, release_id: str) -> dict:
        with self._lock:
            results = self._last_results.get(release_id)
        if results is None:
            raise NoEvaluationError(release_id)
        return {
            "release_id": release_id,
            "passed": all(result.passed for result in results),
            "results": [result.to_dict() for result in results],
        }


_policy_engine = ReleasePolicyEngine()


def get_release_policy_engine() -> ReleasePolicyEngine:
    return _policy_engine


router = APIRouter(prefix="/governance", tags=["governance-release-policies"])


@router.post("/release-policies")
def register_release_policy(payload: dict = Body(...)) -> dict:
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")

    try:
        policy = get_release_policy_engine().register_policy(
            name,
            lambda release: False,
            description=payload.get("description", ""),
        )
    except PolicyAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return policy.to_dict()


@router.post("/releases/{release}/validate")
def validate_release(release: str, payload: dict = Body(default={})) -> dict:
    try:
        results = get_release_policy_engine().evaluate(
            release, overrides=payload.get("overrides")
        )
    except UnknownReleaseError:
        raise HTTPException(status_code=404, detail="unknown release")
    return {
        "release_id": release,
        "passed": all(result.passed for result in results),
        "results": [result.to_dict() for result in results],
    }


@router.get("/releases/{release}/policy")
def get_release_policy_summary(release: str) -> dict:
    try:
        summary = get_release_policy_engine().summary(release)
    except NoEvaluationError:
        raise HTTPException(status_code=404, detail="release has not been validated")
    return summary
