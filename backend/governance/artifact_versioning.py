from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .artifact_registry import ArtifactRegistry, UnknownArtifactError, get_artifact_registry

_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

VersionState = ("ACTIVE", "ROLLED_BACK")


class UnknownVersionError(KeyError):
    pass


class DuplicateVersionError(ValueError):
    pass


class NoRollbackTargetError(RuntimeError):
    pass


def _parse_version(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


@dataclass(frozen=True)
class ArtifactVersion:
    """An immutable record of one lifecycle state of a named artifact's version."""

    name: str
    version: str
    artifact_id: str
    state: str = "ACTIVE"
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "artifact_id": self.artifact_id,
            "state": self.state,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ArtifactVersionManager:
    """Tracks version lifecycle and rollback state for registered artifacts."""

    def __init__(self, registry: Optional[ArtifactRegistry] = None) -> None:
        self._registry = registry or get_artifact_registry()
        self._versions: dict[str, list[ArtifactVersion]] = {}
        self._lock = Lock()

    def create(
        self,
        name: str,
        version: str,
        *,
        timestamp: Optional[datetime] = None,
    ) -> ArtifactVersion:
        if not name:
            raise ValueError("artifact name is required")
        if not _VERSION_PATTERN.match(version or ""):
            raise ValueError("version must follow semantic versioning (e.g. '1.0.0')")

        artifact = self._registry.find(name, version)

        entry = ArtifactVersion(
            name=name,
            version=version,
            artifact_id=artifact.artifact_id,
            state="ACTIVE",
            created_at=timestamp or datetime.now(timezone.utc),
        )
        with self._lock:
            versions = self._versions.setdefault(name, [])
            if any(existing.version == version for existing in versions):
                raise DuplicateVersionError(
                    f"version '{version}' already recorded for '{name}'"
                )
            versions.append(entry)
        return entry

    def history(self, name: str) -> list[ArtifactVersion]:
        with self._lock:
            versions = list(self._versions.get(name, []))
        return sorted(versions, key=lambda entry: _parse_version(entry.version))

    def latest(self, name: str) -> ArtifactVersion:
        active = [entry for entry in self.history(name) if entry.state == "ACTIVE"]
        if not active:
            raise UnknownVersionError(name)
        return max(active, key=lambda entry: _parse_version(entry.version))

    def rollback(
        self,
        name: str,
        *,
        to_version: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> ArtifactVersion:
        current = self.latest(name)

        with self._lock:
            versions = self._versions.get(name, [])
            if to_version is not None:
                target = next(
                    (entry for entry in versions if entry.version == to_version), None
                )
                if target is None:
                    raise UnknownVersionError(f"{name}@{to_version}")
                if target.version == current.version:
                    raise NoRollbackTargetError(
                        f"'{to_version}' is already the latest version"
                    )
            else:
                candidates = [
                    entry
                    for entry in versions
                    if entry.state == "ACTIVE" and entry.version != current.version
                ]
                if not candidates:
                    raise NoRollbackTargetError(
                        f"no rollback target available for '{name}'"
                    )
                target = max(candidates, key=lambda entry: _parse_version(entry.version))

            self._versions[name] = [
                replace(entry, state="ROLLED_BACK") if entry.version == current.version else entry
                for entry in versions
            ]

        return target


_version_manager = ArtifactVersionManager()


def get_artifact_version_manager() -> ArtifactVersionManager:
    return _version_manager


router = APIRouter(prefix="/governance", tags=["governance-artifact-versions"])


@router.post("/artifacts/{artifact}/versions")
def create_version(artifact: str, payload: dict = Body(...)) -> dict:
    version = payload.get("version")
    if not version:
        raise HTTPException(status_code=422, detail="version is required")

    try:
        entry = get_artifact_version_manager().create(artifact, version)
    except UnknownArtifactError:
        raise HTTPException(status_code=404, detail="unknown artifact")
    except DuplicateVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return entry.to_dict()


@router.get("/artifacts/{artifact}/versions")
def list_versions(artifact: str) -> list[dict]:
    return [entry.to_dict() for entry in get_artifact_version_manager().history(artifact)]


@router.get("/artifacts/{artifact}/versions/latest")
def get_latest_version(artifact: str) -> dict:
    try:
        entry = get_artifact_version_manager().latest(artifact)
    except UnknownVersionError:
        raise HTTPException(status_code=404, detail="no versions recorded for artifact")
    return entry.to_dict()
