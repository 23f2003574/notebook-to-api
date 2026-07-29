from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query


class PluginAlreadyRegisteredError(ValueError):
    pass


class UnknownPluginError(KeyError):
    pass


@dataclass(frozen=True)
class PluginMetadata:
    """Descriptive information attached to a registered plugin version."""

    description: str = ""
    author: str = ""
    tags: tuple = ()
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "author": self.author,
            "tags": list(self.tags),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Optional[dict]) -> "PluginMetadata":
        payload = payload or {}
        return cls(
            description=payload.get("description", ""),
            author=payload.get("author", ""),
            tags=tuple(payload.get("tags", ())),
            source=payload.get("source", ""),
        )


@dataclass(frozen=True)
class Plugin:
    """A single registered version of a plugin."""

    name: str
    version: str
    metadata: PluginMetadata
    registered_at: datetime

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "metadata": self.metadata.to_dict(),
            "registered_at": self.registered_at.isoformat(),
        }


class PluginRegistry:
    """Tracks registered plugins, their versions, and discovery metadata."""

    def __init__(self) -> None:
        self._plugins: dict = {}
        self._tag_index: dict = {}
        self._lock = Lock()

    def register(
        self,
        name: str,
        version: str,
        metadata: Optional[PluginMetadata] = None,
    ) -> Plugin:
        if not name:
            raise ValueError("plugin name is required")
        if not version:
            raise ValueError("plugin version is required")
        metadata = metadata or PluginMetadata()
        with self._lock:
            versions = self._plugins.setdefault(name, {})
            if version in versions:
                raise PluginAlreadyRegisteredError(f"{name}@{version} is already registered")
            plugin = Plugin(
                name=name,
                version=version,
                metadata=metadata,
                registered_at=datetime.now(timezone.utc),
            )
            versions[version] = plugin
            for tag in metadata.tags:
                self._tag_index.setdefault(tag, set()).add(name)
        return plugin

    def unregister(self, name: str, version: Optional[str] = None) -> None:
        with self._lock:
            versions = self._plugins.get(name)
            if not versions:
                raise UnknownPluginError(name)
            if version is None:
                removed = self._plugins.pop(name)
            else:
                if version not in versions:
                    raise UnknownPluginError(f"{name}@{version}")
                removed = {version: versions.pop(version)}
                if not versions:
                    del self._plugins[name]
            if name not in self._plugins:
                for plugin in removed.values():
                    for tag in plugin.metadata.tags:
                        names = self._tag_index.get(tag)
                        if names is not None:
                            names.discard(name)
                            if not names:
                                del self._tag_index[tag]

    def get(self, name: str, version: Optional[str] = None) -> Plugin:
        with self._lock:
            versions = self._plugins.get(name)
            if not versions:
                raise UnknownPluginError(name)
            if version is None:
                return max(versions.values(), key=lambda plugin: plugin.registered_at)
            plugin = versions.get(version)
            if plugin is None:
                raise UnknownPluginError(f"{name}@{version}")
            return plugin

    def is_registered(self, name: str, version: Optional[str] = None) -> bool:
        with self._lock:
            versions = self._plugins.get(name)
            if not versions:
                return False
            if version is None:
                return True
            return version in versions

    def list_plugins(self, tag: Optional[str] = None) -> list:
        with self._lock:
            if tag is not None:
                names = sorted(self._tag_index.get(tag, set()))
            else:
                names = sorted(self._plugins)
            result = []
            for name in names:
                versions = self._plugins.get(name)
                if versions:
                    result.append(max(versions.values(), key=lambda plugin: plugin.registered_at))
            return result


_plugin_registry = PluginRegistry()


def get_plugin_registry() -> PluginRegistry:
    return _plugin_registry


router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.post("", status_code=201)
def register_plugin_endpoint(
    payload: dict = Body(default={}),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> dict:
    try:
        plugin = registry.register(
            payload.get("name", ""),
            payload.get("version", ""),
            PluginMetadata.from_dict(payload.get("metadata")),
        )
    except PluginAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return plugin.to_dict()


@router.get("")
def list_plugins_endpoint(
    tag: Optional[str] = Query(default=None),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> list:
    return [plugin.to_dict() for plugin in registry.list_plugins(tag=tag)]


@router.get("/{name}")
def get_plugin_endpoint(
    name: str,
    version: Optional[str] = Query(default=None),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> dict:
    try:
        plugin = registry.get(name, version=version)
    except UnknownPluginError:
        raise HTTPException(status_code=404, detail="unknown plugin")
    return plugin.to_dict()


@router.delete("/{name}", status_code=204)
def unregister_plugin_endpoint(
    name: str,
    version: Optional[str] = Query(default=None),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> None:
    try:
        registry.unregister(name, version=version)
    except UnknownPluginError:
        raise HTTPException(status_code=404, detail="unknown plugin")
