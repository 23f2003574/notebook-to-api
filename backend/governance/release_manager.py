from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable, Optional

from fastapi import APIRouter, Body, HTTPException

from .artifact_promotion import ArtifactPromotionEngine, ENVIRONMENTS, get_artifact_promotion_engine

ReleaseState = ("DRAFT", "PUBLISHED", "CANCELLED")


def _new_id() -> str:
    return uuid.uuid4().hex


class UnknownReleaseError(KeyError):
    pass


class InvalidReleaseStateError(RuntimeError):
    pass


class ArtifactNotProductionReadyError(ValueError):
    pass


@dataclass(frozen=True)
class Release:
    """An immutable snapshot of a software release bundling one or more artifacts."""

    release_id: str
    name: str
    artifacts: tuple = ()
    state: str = "DRAFT"
    created_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "release_id": self.release_id,
            "name": self.name,
            "artifacts": [dict(artifact) for artifact in self.artifacts],
            "state": self.state,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
        }


class ReleaseManager:
    """Creates and tracks the lifecycle of releases built from promoted artifacts."""

    def __init__(self, promotion_engine: Optional[ArtifactPromotionEngine] = None) -> None:
        self._promotion_engine = promotion_engine or get_artifact_promotion_engine()
        self._releases: dict[str, Release] = {}
        self._lock = Lock()

    def create(
        self,
        name: str,
        artifacts: Iterable[dict],
        *,
        timestamp: Optional[datetime] = None,
    ) -> Release:
        if not name:
            raise ValueError("release name is required")

        artifacts = list(artifacts)
        if not artifacts:
            raise ValueError("release must include at least one artifact")

        resolved = []
        for artifact in artifacts:
            artifact_name = artifact.get("name")
            artifact_version = artifact.get("version")
            if not artifact_name or not artifact_version:
                raise ValueError("each artifact requires a name and version")

            current_environment = self._promotion_engine.current_environment(
                artifact_name, artifact_version
            )
            if current_environment != ENVIRONMENTS[-1]:
                raise ArtifactNotProductionReadyError(
                    f"'{artifact_name}@{artifact_version}' has not been promoted to "
                    f"'{ENVIRONMENTS[-1]}' (currently '{current_environment}')"
                )
            resolved.append({"name": artifact_name, "version": artifact_version})

        release = Release(
            release_id=_new_id(),
            name=name,
            artifacts=tuple(resolved),
            state="DRAFT",
            created_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            self._releases[release.release_id] = release
        return release

    def get(self, release_id: str) -> Release:
        with self._lock:
            release = self._releases.get(release_id)
        if release is None:
            raise UnknownReleaseError(release_id)
        return release

    def publish(self, release_id: str, *, timestamp: Optional[datetime] = None) -> Release:
        release = self.get(release_id)
        if release.state != "DRAFT":
            raise InvalidReleaseStateError(
                f"release '{release_id}' is not in DRAFT state (state={release.state})"
            )

        updated = replace(
            release, state="PUBLISHED", published_at=timestamp or datetime.now(timezone.utc)
        )
        with self._lock:
            self._releases[release_id] = updated
        return updated

    def cancel(self, release_id: str, *, timestamp: Optional[datetime] = None) -> Release:
        release = self.get(release_id)
        if release.state != "DRAFT":
            raise InvalidReleaseStateError(
                f"release '{release_id}' is not in DRAFT state (state={release.state})"
            )

        updated = replace(
            release, state="CANCELLED", cancelled_at=timestamp or datetime.now(timezone.utc)
        )
        with self._lock:
            self._releases[release_id] = updated
        return updated

    def history(self) -> list[Release]:
        with self._lock:
            releases = list(self._releases.values())
        return sorted(releases, key=lambda release: release.created_at or datetime.min.replace(tzinfo=timezone.utc))


_release_manager = ReleaseManager()


def get_release_manager() -> ReleaseManager:
    return _release_manager


router = APIRouter(prefix="/governance", tags=["governance-releases"])


@router.post("/releases")
def create_release(payload: dict = Body(...)) -> dict:
    name = payload.get("name")
    artifacts = payload.get("artifacts")
    if not name or not artifacts:
        raise HTTPException(status_code=422, detail="name and artifacts are required")

    try:
        release = get_release_manager().create(name, artifacts)
    except ArtifactNotProductionReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return release.to_dict()


@router.post("/releases/{release}/publish")
def publish_release(release: str) -> dict:
    try:
        updated = get_release_manager().publish(release)
    except UnknownReleaseError:
        raise HTTPException(status_code=404, detail="unknown release")
    except InvalidReleaseStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return updated.to_dict()


@router.get("/releases")
def list_releases() -> list[dict]:
    return [release.to_dict() for release in get_release_manager().history()]


@router.get("/releases/{release}")
def get_release(release: str) -> dict:
    try:
        result = get_release_manager().get(release)
    except UnknownReleaseError:
        raise HTTPException(status_code=404, detail="unknown release")
    return result.to_dict()
