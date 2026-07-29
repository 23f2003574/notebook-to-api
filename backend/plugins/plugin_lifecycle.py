from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .extension_api import (
    ExtensionAPI,
    IncompatibleApiVersionError,
    get_extension_api,
)
from .plugin_loader import (
    ManifestValidationError,
    PluginLoadError,
    PluginLoader,
    PluginManifest,
    get_plugin_loader,
)
from .plugin_registry import PluginMetadata, PluginRegistry, UnknownPluginError, get_plugin_registry


class PluginState(str, Enum):
    INSTALLED = "installed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNINSTALLED = "uninstalled"


class PluginAlreadyInstalledError(ValueError):
    pass


class InvalidTransitionError(ValueError):
    pass


_ALLOWED_TRANSITIONS = {
    PluginState.INSTALLED: {PluginState.ENABLED, PluginState.UNINSTALLED},
    PluginState.ENABLED: {PluginState.DISABLED, PluginState.UNINSTALLED},
    PluginState.DISABLED: {PluginState.ENABLED, PluginState.UNINSTALLED},
    PluginState.UNINSTALLED: set(),
}


@dataclass(frozen=True)
class LifecycleEvent:
    """A single recorded state transition for a plugin."""

    from_state: Optional[PluginState]
    to_state: PluginState
    timestamp: datetime
    reason: str = "manual"

    def to_dict(self) -> dict:
        return {
            "from_state": self.from_state.value if self.from_state else None,
            "to_state": self.to_state.value,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
        }


class PluginLifecycleManager:
    """Drives plugin state from installation through to removal."""

    def __init__(
        self,
        loader: Optional[PluginLoader] = None,
        registry: Optional[PluginRegistry] = None,
        extension_api: Optional[ExtensionAPI] = None,
    ) -> None:
        self._loader = loader if loader is not None else get_plugin_loader()
        self._registry = registry if registry is not None else get_plugin_registry()
        self._extension_api = extension_api if extension_api is not None else get_extension_api()
        self._records: dict = {}
        self._lock = Lock()

    def _require_record(self, name: str) -> dict:
        with self._lock:
            record = self._records.get(name)
        if record is None:
            raise UnknownPluginError(name)
        return record

    def _validate_transition(self, current: PluginState, target: PluginState) -> None:
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise InvalidTransitionError(
                f"cannot transition plugin from '{current.value}' to '{target.value}'"
            )

    def _commit_transition(self, name: str, to_state: PluginState, reason: str) -> LifecycleEvent:
        with self._lock:
            record = self._records[name]
            event = LifecycleEvent(
                from_state=record["state"],
                to_state=to_state,
                timestamp=datetime.now(timezone.utc),
                reason=reason,
            )
            record["state"] = to_state
            record["history"].append(event)
        return event

    def install(self, manifest: PluginManifest, *, reason: str = "manual") -> LifecycleEvent:
        with self._lock:
            record = self._records.get(manifest.name)
            if record is not None and record["state"] != PluginState.UNINSTALLED:
                raise PluginAlreadyInstalledError(manifest.name)
            history = list(record["history"]) if record else []

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

        event = LifecycleEvent(
            from_state=None, to_state=PluginState.INSTALLED, timestamp=datetime.now(timezone.utc), reason=reason
        )
        history.append(event)
        with self._lock:
            self._records[manifest.name] = {
                "state": PluginState.INSTALLED,
                "manifest": manifest,
                "history": history,
            }
        return event

    def enable(self, name: str, *, reason: str = "manual") -> LifecycleEvent:
        record = self._require_record(name)
        self._validate_transition(record["state"], PluginState.ENABLED)
        if not self._loader.is_loaded(name):
            self._loader.load(record["manifest"])
        try:
            self._register_extension_if_declared(name)
        except IncompatibleApiVersionError:
            self._loader.unload_if_loaded(name)
            raise
        return self._commit_transition(name, PluginState.ENABLED, reason)

    def disable(self, name: str, *, reason: str = "manual") -> LifecycleEvent:
        record = self._require_record(name)
        self._validate_transition(record["state"], PluginState.DISABLED)
        self._loader.unload_if_loaded(name)
        self._extension_api.unregister_extension(name)
        return self._commit_transition(name, PluginState.DISABLED, reason)

    def uninstall(self, name: str, *, reason: str = "manual") -> LifecycleEvent:
        record = self._require_record(name)
        self._validate_transition(record["state"], PluginState.UNINSTALLED)
        self._loader.unload_if_loaded(name)
        self._extension_api.unregister_extension(name)
        if self._registry.is_registered(name):
            self._registry.unregister(name)
        return self._commit_transition(name, PluginState.UNINSTALLED, reason)

    def _register_extension_if_declared(self, name: str) -> None:
        """If the plugin's loaded module declares an extension API version, register it.

        Plugins are not required to participate in the extension API; a
        module without an ``EXTENSION_API_VERSION`` attribute simply loads
        and enables without gaining an extension context.
        """
        loaded = self._loader.get_loaded(name)
        api_version = getattr(loaded.module, "EXTENSION_API_VERSION", None)
        if api_version is None:
            return
        capabilities = getattr(loaded.module, "CAPABILITIES", [])
        self._extension_api.register_extension(name, api_version, capabilities)

    def get_state(self, name: str) -> PluginState:
        return self._require_record(name)["state"]

    def get_history(self, name: str) -> list:
        return list(self._require_record(name)["history"])

    def auto_enable_installed(self) -> list:
        """Re-enable every plugin currently sitting in the Installed state.

        Intended to be called on process startup to restore plugins that were
        installed but not yet (re-)enabled, without disturbing plugins an
        operator explicitly disabled.
        """
        with self._lock:
            names = [name for name, record in self._records.items() if record["state"] == PluginState.INSTALLED]
        enabled = []
        for name in names:
            try:
                self.enable(name, reason="startup-auto-enable")
            except PluginLoadError:
                continue
            enabled.append(name)
        return enabled


_plugin_lifecycle_manager = PluginLifecycleManager()


def get_plugin_lifecycle_manager() -> PluginLifecycleManager:
    return _plugin_lifecycle_manager


router = APIRouter(prefix="/plugins", tags=["plugins-lifecycle"])


@router.post("/install", status_code=201)
def install_plugin_endpoint(
    payload: dict = Body(default={}),
    lifecycle: PluginLifecycleManager = Depends(get_plugin_lifecycle_manager),
) -> dict:
    try:
        manifest = PluginManifest.from_dict(payload)
    except ManifestValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        event = lifecycle.install(manifest)
    except PluginAlreadyInstalledError as exc:
        raise HTTPException(status_code=409, detail=f"'{exc}' is already installed")
    return {"name": manifest.name, "state": event.to_state.value}


@router.post("/{plugin}/enable")
def enable_plugin_endpoint(
    plugin: str,
    lifecycle: PluginLifecycleManager = Depends(get_plugin_lifecycle_manager),
) -> dict:
    try:
        event = lifecycle.enable(plugin)
    except UnknownPluginError:
        raise HTTPException(status_code=404, detail="unknown plugin")
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except PluginLoadError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except IncompatibleApiVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"name": plugin, "state": event.to_state.value}


@router.post("/{plugin}/disable")
def disable_plugin_endpoint(
    plugin: str,
    lifecycle: PluginLifecycleManager = Depends(get_plugin_lifecycle_manager),
) -> dict:
    try:
        event = lifecycle.disable(plugin)
    except UnknownPluginError:
        raise HTTPException(status_code=404, detail="unknown plugin")
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"name": plugin, "state": event.to_state.value}


@router.delete("/{plugin}", status_code=204)
def uninstall_plugin_endpoint(
    plugin: str,
    lifecycle: PluginLifecycleManager = Depends(get_plugin_lifecycle_manager),
) -> None:
    try:
        lifecycle.uninstall(plugin)
    except UnknownPluginError:
        raise HTTPException(status_code=404, detail="unknown plugin")
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
