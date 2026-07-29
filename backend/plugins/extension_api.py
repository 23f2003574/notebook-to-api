from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Callable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

API_VERSION = "1.0"


class IncompatibleApiVersionError(ValueError):
    pass


class UnknownExtensionError(KeyError):
    pass


class UnknownEndpointError(KeyError):
    pass


class EndpointAlreadyRegisteredError(ValueError):
    pass


class UnknownServiceError(KeyError):
    pass


class ServiceAlreadyRegisteredError(ValueError):
    pass


def _major_version(version: str) -> str:
    return version.split(".")[0]


def is_compatible_version(version: str) -> bool:
    """A plugin's declared extension API version is compatible if its major version matches the host's."""
    return _major_version(version) == _major_version(API_VERSION)


@dataclass(frozen=True)
class ExtensionCapability:
    """A named capability an extension advertises to the host and other extensions."""

    name: str
    description: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description}

    @classmethod
    def from_value(cls, value) -> "ExtensionCapability":
        if isinstance(value, ExtensionCapability):
            return value
        if isinstance(value, dict):
            return cls(name=value["name"], description=value.get("description", ""))
        return cls(name=str(value))


@dataclass(frozen=True)
class ExtensionContext:
    """A plugin's registered presence in the extension API."""

    plugin: str
    api_version: str
    capabilities: tuple
    registered_at: datetime

    def to_dict(self) -> dict:
        return {
            "plugin": self.plugin,
            "api_version": self.api_version,
            "capabilities": [capability.to_dict() for capability in self.capabilities],
            "registered_at": self.registered_at.isoformat(),
        }


class ExtensionAPI:
    """The stable surface plugins use to expose endpoints and services to the host."""

    def __init__(self) -> None:
        self._extensions: dict = {}
        self._endpoints: dict = {}
        self._services: dict = {}
        self._lock = Lock()

    def register_extension(
        self,
        plugin: str,
        api_version: str,
        capabilities: Optional[list] = None,
    ) -> ExtensionContext:
        if not plugin:
            raise ValueError("plugin name is required")
        if not is_compatible_version(api_version):
            raise IncompatibleApiVersionError(
                f"extension api version '{api_version}' is not compatible with host version '{API_VERSION}'"
            )
        context = ExtensionContext(
            plugin=plugin,
            api_version=api_version,
            capabilities=tuple(ExtensionCapability.from_value(c) for c in (capabilities or [])),
            registered_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._extensions[plugin] = context
            self._endpoints.setdefault(plugin, {})
        return context

    def unregister_extension(self, plugin: str) -> None:
        with self._lock:
            self._extensions.pop(plugin, None)
            self._endpoints.pop(plugin, None)
            stale_services = [name for name, (owner, _service) in self._services.items() if owner == plugin]
            for name in stale_services:
                del self._services[name]

    def get_extension(self, plugin: str) -> ExtensionContext:
        with self._lock:
            context = self._extensions.get(plugin)
        if context is None:
            raise UnknownExtensionError(plugin)
        return context

    def list_extensions(self) -> list:
        with self._lock:
            return [self._extensions[name] for name in sorted(self._extensions)]

    def register_endpoint(self, plugin: str, name: str, handler: Callable) -> None:
        with self._lock:
            if plugin not in self._extensions:
                raise UnknownExtensionError(plugin)
            endpoints = self._endpoints.setdefault(plugin, {})
            if name in endpoints:
                raise EndpointAlreadyRegisteredError(f"{plugin}:{name}")
            endpoints[name] = handler

    def register_service(self, plugin: str, name: str, service) -> None:
        with self._lock:
            if plugin not in self._extensions:
                raise UnknownExtensionError(plugin)
            if name in self._services:
                raise ServiceAlreadyRegisteredError(name)
            self._services[name] = (plugin, service)

    def get_service(self, name: str):
        with self._lock:
            entry = self._services.get(name)
        if entry is None:
            raise UnknownServiceError(name)
        return entry[1]

    def invoke(self, plugin: str, endpoint: str, *args, **kwargs):
        with self._lock:
            endpoints = self._endpoints.get(plugin)
            if endpoints is None:
                raise UnknownExtensionError(plugin)
            handler = endpoints.get(endpoint)
        if handler is None:
            raise UnknownEndpointError(f"{plugin}:{endpoint}")
        return handler(*args, **kwargs)


_extension_api = ExtensionAPI()


def get_extension_api() -> ExtensionAPI:
    return _extension_api


router = APIRouter(prefix="/plugins/extensions", tags=["plugins-extensions"])


@router.post("", status_code=201)
def register_extension_endpoint(
    payload: dict = Body(default={}),
    api: ExtensionAPI = Depends(get_extension_api),
) -> dict:
    plugin = payload.get("plugin", "")
    api_version = payload.get("api_version", "")
    if not plugin or not api_version:
        raise HTTPException(status_code=422, detail="plugin and api_version are required")
    try:
        context = api.register_extension(plugin, api_version, payload.get("capabilities"))
    except IncompatibleApiVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return context.to_dict()


@router.get("")
def list_extensions_endpoint(api: ExtensionAPI = Depends(get_extension_api)) -> list:
    return [context.to_dict() for context in api.list_extensions()]


@router.get("/{extension}")
def get_extension_endpoint(
    extension: str,
    api: ExtensionAPI = Depends(get_extension_api),
) -> dict:
    try:
        context = api.get_extension(extension)
    except UnknownExtensionError:
        raise HTTPException(status_code=404, detail="unknown extension")
    return context.to_dict()
