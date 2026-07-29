from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .plugin_registry import PluginMetadata, PluginRegistry, get_plugin_registry

_ENTRY_POINT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


class ManifestValidationError(ValueError):
    pass


class PluginLoadError(RuntimeError):
    pass


class PluginAlreadyLoadedError(ValueError):
    pass


class PluginNotLoadedError(KeyError):
    pass


def _validate_manifest_fields(data: dict) -> None:
    for field_name in ("name", "version", "entry_point"):
        if not data.get(field_name):
            raise ManifestValidationError(f"manifest is missing required field '{field_name}'")
    entry_point = data["entry_point"]
    if not _ENTRY_POINT_PATTERN.match(entry_point):
        raise ManifestValidationError(f"'{entry_point}' is not a valid entry point")


@dataclass(frozen=True)
class PluginManifest:
    """Declares how a plugin should be discovered and loaded."""

    name: str
    version: str
    entry_point: str
    description: str = ""
    author: str = ""
    tags: tuple = ()

    def __post_init__(self) -> None:
        _validate_manifest_fields(
            {"name": self.name, "version": self.version, "entry_point": self.entry_point}
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "entry_point": self.entry_point,
            "description": self.description,
            "author": self.author,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PluginManifest":
        _validate_manifest_fields(payload)
        return cls(
            name=payload["name"],
            version=payload["version"],
            entry_point=payload["entry_point"],
            description=payload.get("description", ""),
            author=payload.get("author", ""),
            tags=tuple(payload.get("tags", ())),
        )


@dataclass(frozen=True)
class LoadedPlugin:
    """A plugin manifest bound to its imported module."""

    manifest: PluginManifest
    module: ModuleType
    loaded_at: datetime

    def to_dict(self) -> dict:
        return {
            **self.manifest.to_dict(),
            "loaded_at": self.loaded_at.isoformat(),
        }


class PluginLoader:
    """Discovers plugin manifests and loads them into the runtime."""

    def __init__(self, registry: Optional[PluginRegistry] = None) -> None:
        self._registry = registry if registry is not None else get_plugin_registry()
        self._loaded: dict = {}
        self._lock = Lock()

    def discover(self, directory: str) -> list:
        manifests = []
        for manifest_path in sorted(Path(directory).glob("*.plugin.json")):
            try:
                data = json.loads(manifest_path.read_text())
            except json.JSONDecodeError as exc:
                raise ManifestValidationError(f"'{manifest_path}' is not valid JSON") from exc
            manifests.append(PluginManifest.from_dict(data))
        return manifests

    def load(self, manifest: PluginManifest) -> LoadedPlugin:
        with self._lock:
            if manifest.name in self._loaded:
                raise PluginAlreadyLoadedError(manifest.name)
        try:
            module = importlib.import_module(manifest.entry_point)
        except ImportError as exc:
            raise PluginLoadError(f"failed to import '{manifest.entry_point}': {exc}") from exc
        loaded = LoadedPlugin(manifest=manifest, module=module, loaded_at=datetime.now(timezone.utc))
        with self._lock:
            self._loaded[manifest.name] = loaded
        if not self._registry.is_registered(manifest.name, manifest.version):
            self._registry.register(
                manifest.name,
                manifest.version,
                PluginMetadata(
                    description=manifest.description,
                    author=manifest.author,
                    tags=manifest.tags,
                ),
            )
        return loaded

    def reload(self, name: str) -> LoadedPlugin:
        with self._lock:
            loaded = self._loaded.get(name)
        if loaded is None:
            raise PluginNotLoadedError(name)
        try:
            module = importlib.reload(loaded.module)
        except ImportError as exc:
            raise PluginLoadError(f"failed to reload '{loaded.manifest.entry_point}': {exc}") from exc
        refreshed = LoadedPlugin(
            manifest=loaded.manifest, module=module, loaded_at=datetime.now(timezone.utc)
        )
        with self._lock:
            self._loaded[name] = refreshed
        return refreshed

    def unload(self, name: str) -> None:
        with self._lock:
            if name not in self._loaded:
                raise PluginNotLoadedError(name)
            del self._loaded[name]

    def is_loaded(self, name: str) -> bool:
        with self._lock:
            return name in self._loaded

    def list_loaded(self) -> list:
        with self._lock:
            return sorted(self._loaded.values(), key=lambda loaded: loaded.manifest.name)


_plugin_loader = PluginLoader()


def get_plugin_loader() -> PluginLoader:
    return _plugin_loader


router = APIRouter(prefix="/plugins", tags=["plugins-loader"])


@router.post("/load", status_code=201)
def load_plugin_endpoint(
    payload: dict = Body(default={}),
    loader: PluginLoader = Depends(get_plugin_loader),
) -> dict:
    try:
        manifest = PluginManifest.from_dict(payload)
    except ManifestValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        loaded = loader.load(manifest)
    except PluginAlreadyLoadedError as exc:
        raise HTTPException(status_code=409, detail=f"'{exc}' is already loaded")
    except PluginLoadError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return loaded.to_dict()


@router.post("/reload/{plugin}")
def reload_plugin_endpoint(
    plugin: str,
    loader: PluginLoader = Depends(get_plugin_loader),
) -> dict:
    try:
        loaded = loader.reload(plugin)
    except PluginNotLoadedError:
        raise HTTPException(status_code=404, detail="plugin is not loaded")
    except PluginLoadError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return loaded.to_dict()


@router.post("/unload/{plugin}", status_code=204)
def unload_plugin_endpoint(
    plugin: str,
    loader: PluginLoader = Depends(get_plugin_loader),
) -> None:
    try:
        loader.unload(plugin)
    except PluginNotLoadedError:
        raise HTTPException(status_code=404, detail="plugin is not loaded")


@router.get("/loaded")
def list_loaded_plugins_endpoint(
    loader: PluginLoader = Depends(get_plugin_loader),
) -> list:
    return [loaded.to_dict() for loaded in loader.list_loaded()]
