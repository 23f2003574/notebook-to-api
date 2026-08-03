from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query


class ModelAlreadyRegisteredError(ValueError):
    pass


class UnknownModelError(KeyError):
    pass


@dataclass(frozen=True)
class ModelMetadata:
    """Descriptive information attached to a registered model version."""

    description: str = ""
    provider: str = ""
    capabilities: tuple = ()
    source: str = ""
    entry_point: str = ""

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "provider": self.provider,
            "capabilities": list(self.capabilities),
            "source": self.source,
            "entry_point": self.entry_point,
        }

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "ModelMetadata":
        payload = payload or {}
        return cls(
            description=payload.get("description", ""),
            provider=payload.get("provider", ""),
            capabilities=tuple(payload.get("capabilities", ())),
            source=payload.get("source", ""),
            entry_point=payload.get("entry_point", ""),
        )


@dataclass(frozen=True)
class ModelInfo:
    """A single registered version of a model."""

    name: str
    version: str
    metadata: ModelMetadata
    registered_at: datetime

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "metadata": self.metadata.to_dict(),
            "registered_at": self.registered_at.isoformat(),
        }


class ModelRegistry:
    """Tracks registered models, their versions, and capability metadata."""

    def __init__(self) -> None:
        self._models: dict = {}
        self._capability_index: dict = {}
        self._lock = Lock()

    def register(
        self,
        name: str,
        version: str,
        metadata: Optional[ModelMetadata] = None,
    ) -> ModelInfo:
        if not name:
            raise ValueError("model name is required")
        if not version:
            raise ValueError("model version is required")
        metadata = metadata or ModelMetadata()
        with self._lock:
            versions = self._models.setdefault(name, {})
            if version in versions:
                raise ModelAlreadyRegisteredError(f"{name}@{version} is already registered")
            model = ModelInfo(
                name=name,
                version=version,
                metadata=metadata,
                registered_at=datetime.now(timezone.utc),
            )
            versions[version] = model
            for capability in metadata.capabilities:
                self._capability_index.setdefault(capability, set()).add(name)
        return model

    def remove(self, name: str, version: Optional[str] = None) -> None:
        with self._lock:
            versions = self._models.get(name)
            if not versions:
                raise UnknownModelError(name)
            if version is None:
                removed = self._models.pop(name)
            else:
                if version not in versions:
                    raise UnknownModelError(f"{name}@{version}")
                removed = {version: versions.pop(version)}
                if not versions:
                    del self._models[name]
            if name not in self._models:
                for model in removed.values():
                    for capability in model.metadata.capabilities:
                        names = self._capability_index.get(capability)
                        if names is not None:
                            names.discard(name)
                            if not names:
                                del self._capability_index[capability]

    def get(self, name: str, version: Optional[str] = None) -> ModelInfo:
        with self._lock:
            versions = self._models.get(name)
            if not versions:
                raise UnknownModelError(name)
            if version is None:
                return max(versions.values(), key=lambda model: model.registered_at)
            model = versions.get(version)
            if model is None:
                raise UnknownModelError(f"{name}@{version}")
            return model

    def versions(self, name: str) -> list:
        with self._lock:
            registered = self._models.get(name)
            if not registered:
                raise UnknownModelError(name)
            return sorted(registered.values(), key=lambda model: model.registered_at)

    def is_registered(self, name: str, version: Optional[str] = None) -> bool:
        with self._lock:
            versions = self._models.get(name)
            if not versions:
                return False
            if version is None:
                return True
            return version in versions

    def list_models(self, capability: Optional[str] = None) -> list:
        with self._lock:
            if capability is not None:
                names = sorted(self._capability_index.get(capability, set()))
            else:
                names = sorted(self._models)
            result = []
            for name in names:
                versions = self._models.get(name)
                if versions:
                    result.append(max(versions.values(), key=lambda model: model.registered_at))
            return result


_model_registry = ModelRegistry()


def get_model_registry() -> ModelRegistry:
    return _model_registry


router = APIRouter(prefix="/ai/models", tags=["models"])


@router.post("", status_code=201)
def register_model_endpoint(
    payload: dict = Body(default={}),
    registry: ModelRegistry = Depends(get_model_registry),
) -> dict:
    try:
        model = registry.register(
            payload.get("name", ""),
            payload.get("version", ""),
            ModelMetadata.from_dict(payload.get("metadata")),
        )
    except ModelAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return model.to_dict()


@router.get("")
def list_models_endpoint(
    capability: Optional[str] = Query(default=None),
    registry: ModelRegistry = Depends(get_model_registry),
) -> list:
    return [model.to_dict() for model in registry.list_models(capability=capability)]


@router.get("/{name}")
def get_model_endpoint(
    name: str,
    version: Optional[str] = Query(default=None),
    registry: ModelRegistry = Depends(get_model_registry),
) -> dict:
    try:
        model = registry.get(name, version=version)
    except UnknownModelError:
        raise HTTPException(status_code=404, detail="unknown model")
    return model.to_dict()


@router.delete("/{name}", status_code=204)
def remove_model_endpoint(
    name: str,
    version: Optional[str] = Query(default=None),
    registry: ModelRegistry = Depends(get_model_registry),
) -> None:
    try:
        registry.remove(name, version=version)
    except UnknownModelError:
        raise HTTPException(status_code=404, detail="unknown model")
