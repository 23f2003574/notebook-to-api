from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .model_registry import ModelRegistry, UnknownModelError, get_model_registry


class ModelValidationError(ValueError):
    pass


class ModelNotLoadedError(KeyError):
    pass


@dataclass(frozen=True)
class ModelManifest:
    """Validated load instructions derived from a registered model's metadata."""

    name: str
    version: str
    entry_point: str

    def validate(self) -> None:
        if not self.entry_point:
            raise ModelValidationError(f"model '{self.name}@{self.version}' has no entry_point defined")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "entry_point": self.entry_point,
        }


@dataclass(frozen=True)
class LoadedModel:
    """A model that has been validated and brought into memory."""

    name: str
    version: str
    manifest: ModelManifest
    loaded_at: datetime

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "manifest": self.manifest.to_dict(),
            "loaded_at": self.loaded_at.isoformat(),
        }


class ModelLoader:
    """Discovers registered models and manages their in-memory load state."""

    def __init__(self) -> None:
        self._loaded: dict = {}
        self._lock = Lock()

    def discover(self, registry: ModelRegistry) -> list:
        with self._lock:
            loaded_names = set(self._loaded)
        manifests = []
        for model in registry.list_models():
            if model.name in loaded_names:
                continue
            manifests.append(
                ModelManifest(
                    name=model.name,
                    version=model.version,
                    entry_point=model.metadata.entry_point,
                )
            )
        return manifests

    def load(
        self,
        name: str,
        *,
        registry: ModelRegistry,
        version: Optional[str] = None,
    ) -> LoadedModel:
        with self._lock:
            existing = self._loaded.get(name)
            if existing is not None and (version is None or existing.version == version):
                return existing

        model = registry.get(name, version=version)
        manifest = ModelManifest(
            name=model.name,
            version=model.version,
            entry_point=model.metadata.entry_point,
        )
        manifest.validate()

        loaded = LoadedModel(
            name=model.name,
            version=model.version,
            manifest=manifest,
            loaded_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._loaded[name] = loaded
        return loaded

    def reload(
        self,
        name: str,
        *,
        registry: ModelRegistry,
        version: Optional[str] = None,
    ) -> LoadedModel:
        with self._lock:
            if name not in self._loaded:
                raise ModelNotLoadedError(name)
            del self._loaded[name]
        return self.load(name, registry=registry, version=version)

    def unload(self, name: str) -> None:
        with self._lock:
            if name not in self._loaded:
                raise ModelNotLoadedError(name)
            del self._loaded[name]

    def get(self, name: str) -> LoadedModel:
        with self._lock:
            loaded = self._loaded.get(name)
        if loaded is None:
            raise ModelNotLoadedError(name)
        return loaded

    def is_loaded(self, name: str) -> bool:
        with self._lock:
            return name in self._loaded

    def list_loaded(self) -> list:
        with self._lock:
            return sorted(self._loaded.values(), key=lambda model: model.name)


_model_loader = ModelLoader()


def get_model_loader() -> ModelLoader:
    return _model_loader


router = APIRouter(prefix="/ai/models", tags=["model-loading"])


@router.post("/load")
def load_model_endpoint(
    payload: dict = Body(default={}),
    loader: ModelLoader = Depends(get_model_loader),
    registry: ModelRegistry = Depends(get_model_registry),
) -> dict:
    try:
        loaded = loader.load(
            payload.get("name", ""),
            registry=registry,
            version=payload.get("version"),
        )
    except UnknownModelError:
        raise HTTPException(status_code=404, detail="unknown model")
    except ModelValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return loaded.to_dict()


@router.post("/reload/{model}")
def reload_model_endpoint(
    model: str,
    loader: ModelLoader = Depends(get_model_loader),
    registry: ModelRegistry = Depends(get_model_registry),
) -> dict:
    try:
        loaded = loader.reload(model, registry=registry)
    except ModelNotLoadedError:
        raise HTTPException(status_code=404, detail="model is not loaded")
    except UnknownModelError:
        raise HTTPException(status_code=404, detail="unknown model")
    except ModelValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return loaded.to_dict()


@router.post("/unload/{model}", status_code=204)
def unload_model_endpoint(
    model: str,
    loader: ModelLoader = Depends(get_model_loader),
) -> None:
    try:
        loader.unload(model)
    except ModelNotLoadedError:
        raise HTTPException(status_code=404, detail="model is not loaded")


@router.get("/loaded")
def list_loaded_models_endpoint(
    loader: ModelLoader = Depends(get_model_loader),
) -> list:
    return [model.to_dict() for model in loader.list_loaded()]
