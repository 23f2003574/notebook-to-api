from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .artifact_registry import ArtifactRegistry, get_artifact_registry


def _new_id() -> str:
    return uuid.uuid4().hex


class RetentionPolicyAlreadyExistsError(ValueError):
    pass


class UnknownRetentionPolicyError(KeyError):
    pass


@dataclass(frozen=True)
class RetentionPolicy:
    """An immutable retention rule scoped to one artifact name."""

    policy_id: str
    name: str
    max_age_seconds: Optional[float] = None
    max_versions: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "max_age_seconds": self.max_age_seconds,
            "max_versions": self.max_versions,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class RetentionResult:
    """A report of one retention pass over a single artifact name."""

    name: str
    retained: tuple = ()
    archived: tuple = ()
    deleted: tuple = ()
    evaluated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "retained": list(self.retained),
            "archived": list(self.archived),
            "deleted": list(self.deleted),
            "evaluated_at": self.evaluated_at.isoformat() if self.evaluated_at else None,
        }


class ArtifactRetentionManager:
    """Applies age- and version-based retention policies to registered artifacts."""

    def __init__(self, registry: Optional[ArtifactRegistry] = None) -> None:
        self._registry = registry or get_artifact_registry()
        self._policies: dict[str, RetentionPolicy] = {}
        self._lock = Lock()

    def register_policy(
        self,
        name: str,
        *,
        max_age_seconds: Optional[float] = None,
        max_versions: Optional[int] = None,
        timestamp: Optional[datetime] = None,
    ) -> RetentionPolicy:
        if not name:
            raise ValueError("artifact name is required")
        if max_age_seconds is None and max_versions is None:
            raise ValueError("policy must define max_age_seconds or max_versions")
        if max_age_seconds is not None and max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        if max_versions is not None and max_versions < 1:
            raise ValueError("max_versions must be at least 1")

        policy = RetentionPolicy(
            policy_id=_new_id(),
            name=name,
            max_age_seconds=max_age_seconds,
            max_versions=max_versions,
            created_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            if name in self._policies:
                raise RetentionPolicyAlreadyExistsError(
                    f"a retention policy for '{name}' already exists"
                )
            self._policies[name] = policy
        return policy

    def policies(self) -> list[RetentionPolicy]:
        with self._lock:
            return list(self._policies.values())

    def archive(self, artifact_id: str, *, timestamp: Optional[datetime] = None) -> None:
        self._registry.archive(artifact_id, timestamp=timestamp)

    def apply(self, name: str, *, timestamp: Optional[datetime] = None) -> RetentionResult:
        with self._lock:
            policy = self._policies.get(name)
        if policy is None:
            raise UnknownRetentionPolicyError(name)

        now = timestamp or datetime.now(timezone.utc)
        artifacts = sorted(
            self._registry.search(name=name),
            key=lambda artifact: artifact.created_at or now,
        )

        candidates: set = set()

        if policy.max_versions is not None and len(artifacts) > policy.max_versions:
            excess = artifacts[: len(artifacts) - policy.max_versions]
            candidates.update(artifact.artifact_id for artifact in excess)

        if policy.max_age_seconds is not None:
            for artifact in artifacts:
                age_seconds = (now - artifact.created_at).total_seconds() if artifact.created_at else 0
                if age_seconds > policy.max_age_seconds:
                    candidates.add(artifact.artifact_id)

        archived_ids = []
        for artifact_id in candidates:
            self.archive(artifact_id, timestamp=now)
            archived_ids.append(artifact_id)

        retained_ids = [
            artifact.artifact_id for artifact in artifacts if artifact.artifact_id not in candidates
        ]

        return RetentionResult(
            name=name,
            retained=tuple(retained_ids),
            archived=tuple(archived_ids),
            deleted=(),
            evaluated_at=now,
        )

    def cleanup(
        self, name: Optional[str] = None, *, timestamp: Optional[datetime] = None
    ) -> list[RetentionResult]:
        now = timestamp or datetime.now(timezone.utc)
        names = [name] if name is not None else [policy.name for policy in self.policies()]

        results = []
        for target_name in names:
            archived_artifacts = [
                artifact
                for artifact in self._registry.search(name=target_name)
                if artifact.archived_at is not None
            ]
            deleted_ids = []
            for artifact in archived_artifacts:
                self._registry.remove(artifact.artifact_id)
                deleted_ids.append(artifact.artifact_id)

            remaining_ids = [
                artifact.artifact_id for artifact in self._registry.search(name=target_name)
            ]
            results.append(
                RetentionResult(
                    name=target_name,
                    retained=tuple(remaining_ids),
                    archived=(),
                    deleted=tuple(deleted_ids),
                    evaluated_at=now,
                )
            )
        return results


_retention_manager = ArtifactRetentionManager()


def get_artifact_retention_manager() -> ArtifactRetentionManager:
    return _retention_manager


router = APIRouter(prefix="/governance", tags=["governance-artifact-retention"])


@router.post("/artifact-retention/policies")
def register_retention_policy(payload: dict = Body(...)) -> dict:
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")

    try:
        policy = get_artifact_retention_manager().register_policy(
            name,
            max_age_seconds=payload.get("max_age_seconds"),
            max_versions=payload.get("max_versions"),
        )
    except RetentionPolicyAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return policy.to_dict()


@router.get("/artifacts/retention")
def list_retention_policies() -> list[dict]:
    return [policy.to_dict() for policy in get_artifact_retention_manager().policies()]


@router.post("/artifacts/cleanup")
def cleanup_artifacts(payload: dict = Body(default={})) -> list[dict]:
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")

    manager = get_artifact_retention_manager()
    try:
        manager.apply(name)
    except UnknownRetentionPolicyError:
        raise HTTPException(
            status_code=404, detail=f"no retention policy registered for '{name}'"
        )

    return [result.to_dict() for result in manager.cleanup(name)]
