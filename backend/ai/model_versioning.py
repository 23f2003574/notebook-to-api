from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .model_registry import (
    ModelAlreadyRegisteredError,
    ModelMetadata,
    ModelRegistry,
    UnknownModelError,
    get_model_registry,
)


class InvalidVersionError(ValueError):
    pass


class VersionNotFoundError(KeyError):
    pass


@dataclass(frozen=True)
class ModelVersion:
    """A resolved version of a model, with its active status against this manager."""

    name: str
    version: str
    is_active: bool
    registered_at: datetime
    metadata: ModelMetadata

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "is_active": self.is_active,
            "registered_at": self.registered_at.isoformat(),
            "metadata": self.metadata.to_dict(),
        }


@dataclass(frozen=True)
class VersionRecord:
    """An entry in a model's version history: a creation or rollback event."""

    name: str
    version: str
    action: str
    occurred_at: datetime

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "action": self.action,
            "occurred_at": self.occurred_at.isoformat(),
        }


class ModelVersionManager:
    """Tracks the active version of each model and its create/rollback history."""

    def __init__(self) -> None:
        self._active: dict = {}
        self._history: dict = {}
        self._lock = Lock()

    @staticmethod
    def _parse_semver(version: str) -> tuple:
        parts = version.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise InvalidVersionError(
                f"'{version}' is not a valid semantic version (expected MAJOR.MINOR.PATCH)"
            )
        return tuple(int(part) for part in parts)

    def create(
        self,
        name: str,
        version: str,
        metadata: Optional[ModelMetadata] = None,
        *,
        registry: ModelRegistry,
    ) -> ModelVersion:
        parsed = self._parse_semver(version)
        if registry.is_registered(name):
            existing_max = max(self._parse_semver(model.version) for model in registry.versions(name))
            if parsed <= existing_max:
                raise InvalidVersionError(
                    f"version '{version}' must be greater than the existing latest "
                    f"'{'.'.join(str(part) for part in existing_max)}'"
                )

        model = registry.register(name, version, metadata)
        occurred_at = datetime.now(timezone.utc)
        with self._lock:
            self._active[name] = version
            self._history.setdefault(name, []).append(
                VersionRecord(name=name, version=version, action="created", occurred_at=occurred_at)
            )
        return ModelVersion(
            name=name,
            version=version,
            is_active=True,
            registered_at=model.registered_at,
            metadata=model.metadata,
        )

    def latest(self, name: str, *, registry: ModelRegistry) -> ModelVersion:
        versions = registry.versions(name)
        best = max(versions, key=lambda model: self._parse_semver(model.version))
        with self._lock:
            active_version = self._active.get(name)
        return ModelVersion(
            name=name,
            version=best.version,
            is_active=(best.version == active_version),
            registered_at=best.registered_at,
            metadata=best.metadata,
        )

    def rollback(
        self,
        name: str,
        target_version: Optional[str] = None,
        *,
        registry: ModelRegistry,
    ) -> ModelVersion:
        versions = registry.versions(name)
        by_version = {model.version: model for model in versions}

        if target_version is not None:
            if target_version not in by_version:
                raise VersionNotFoundError(f"{name}@{target_version}")
            target = target_version
        else:
            with self._lock:
                current_active = self._active.get(name)
            ordered = sorted(versions, key=lambda model: self._parse_semver(model.version), reverse=True)
            ordered_versions = [model.version for model in ordered]
            if current_active not in ordered_versions:
                raise ValueError(f"no active version set for '{name}'")
            index = ordered_versions.index(current_active)
            if index + 1 >= len(ordered_versions):
                raise ValueError(f"no earlier version to roll back to for '{name}'")
            target = ordered_versions[index + 1]

        occurred_at = datetime.now(timezone.utc)
        with self._lock:
            self._active[name] = target
            self._history.setdefault(name, []).append(
                VersionRecord(name=name, version=target, action="rollback", occurred_at=occurred_at)
            )
        model = by_version[target]
        return ModelVersion(
            name=name,
            version=target,
            is_active=True,
            registered_at=model.registered_at,
            metadata=model.metadata,
        )

    def history(self, name: str) -> list:
        with self._lock:
            records = self._history.get(name)
        if records is None:
            raise UnknownModelError(name)
        return list(records)


_model_version_manager = ModelVersionManager()


def get_model_version_manager() -> ModelVersionManager:
    return _model_version_manager


router = APIRouter(prefix="/ai/models", tags=["model-versioning"])


@router.post("/{model}/versions", status_code=201)
def create_version_endpoint(
    model: str,
    payload: dict = Body(default={}),
    manager: ModelVersionManager = Depends(get_model_version_manager),
    registry: ModelRegistry = Depends(get_model_registry),
) -> dict:
    try:
        version = manager.create(
            model,
            payload.get("version", ""),
            ModelMetadata.from_dict(payload.get("metadata")),
            registry=registry,
        )
    except InvalidVersionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ModelAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return version.to_dict()


@router.get("/{model}/versions")
def list_version_history_endpoint(
    model: str,
    manager: ModelVersionManager = Depends(get_model_version_manager),
) -> list:
    try:
        records = manager.history(model)
    except UnknownModelError:
        raise HTTPException(status_code=404, detail="no version history for model")
    return [record.to_dict() for record in records]


@router.get("/{model}/versions/latest")
def latest_version_endpoint(
    model: str,
    manager: ModelVersionManager = Depends(get_model_version_manager),
    registry: ModelRegistry = Depends(get_model_registry),
) -> dict:
    try:
        version = manager.latest(model, registry=registry)
    except UnknownModelError:
        raise HTTPException(status_code=404, detail="unknown model")
    return version.to_dict()


@router.post("/{model}/rollback")
def rollback_endpoint(
    model: str,
    payload: dict = Body(default={}),
    manager: ModelVersionManager = Depends(get_model_version_manager),
    registry: ModelRegistry = Depends(get_model_registry),
) -> dict:
    try:
        version = manager.rollback(model, payload.get("version"), registry=registry)
    except UnknownModelError:
        raise HTTPException(status_code=404, detail="unknown model")
    except VersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return version.to_dict()
