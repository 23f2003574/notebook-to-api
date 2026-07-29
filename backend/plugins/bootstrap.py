from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

REQUIRED_SERVICES: tuple = (
    "plugin_registry",
    "plugin_loader",
    "plugin_lifecycle_manager",
    "extension_api",
    "hook_event_system",
    "plugin_dependency_manager",
    "plugin_configuration_manager",
    "plugin_sandbox",
    "plugin_marketplace_service",
    "plugin_analytics_service",
    "plugin_dashboard_api",
    "plugin_packaging_service",
)

SUBSYSTEM_NAME = "plugin_extension_framework"

PLUGINS_DIR = "plugins"


class UnknownBootstrapServiceError(KeyError):
    """Raised for an unknown bootstrap-registered subsystem name.

    Named distinctly from extension_api.UnknownServiceError, which is a
    different concept (an unknown service registered via a plugin's
    ExtensionAPI.register_service()) - both would otherwise collide under
    the same re-exported name in backend.plugins.__init__.
    """


@dataclass(frozen=True)
class PluginBootstrapValidationResult:
    """One immutable outcome of validating the plugin framework's startup wiring."""

    valid: bool
    registered_services: tuple = field(default_factory=tuple)
    missing_services: tuple = field(default_factory=tuple)
    checked_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "registered_services": list(self.registered_services),
            "missing_services": list(self.missing_services),
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
        }


class PluginBootstrapError(RuntimeError):
    """Raised when the plugin framework fails startup validation."""

    def __init__(self, result: PluginBootstrapValidationResult) -> None:
        self.result = result
        detail = (
            f" (missing: {', '.join(result.missing_services)})" if result.missing_services else ""
        )
        super().__init__("plugin framework bootstrap validation failed" + detail)


class PluginBootstrap:
    """Wires together every Plugin & Extension Framework service singleton and validates startup."""

    def __init__(self) -> None:
        self._services: dict = {}
        self._lock = Lock()

    def register(self) -> dict:
        from .event_system import get_hook_event_system
        from .extension_api import get_extension_api
        from .dashboard import get_plugin_dashboard_api
        from .plugin_analytics import get_plugin_analytics_service
        from .plugin_config import get_plugin_configuration_manager
        from .plugin_dependencies import get_plugin_dependency_manager
        from .plugin_lifecycle import get_plugin_lifecycle_manager
        from .plugin_loader import get_plugin_loader
        from .plugin_marketplace import get_plugin_marketplace_service
        from .plugin_packaging import get_plugin_packaging_service
        from .plugin_registry import get_plugin_registry
        from .plugin_sandbox import get_plugin_sandbox

        services = {
            "plugin_registry": get_plugin_registry(),
            "plugin_loader": get_plugin_loader(),
            "plugin_lifecycle_manager": get_plugin_lifecycle_manager(),
            "extension_api": get_extension_api(),
            "hook_event_system": get_hook_event_system(),
            "plugin_dependency_manager": get_plugin_dependency_manager(),
            "plugin_configuration_manager": get_plugin_configuration_manager(),
            "plugin_sandbox": get_plugin_sandbox(),
            "plugin_marketplace_service": get_plugin_marketplace_service(),
            "plugin_analytics_service": get_plugin_analytics_service(),
            "plugin_dashboard_api": get_plugin_dashboard_api(),
            "plugin_packaging_service": get_plugin_packaging_service(),
        }
        with self._lock:
            self._services = services
        return dict(services)

    def registered_services(self) -> dict:
        with self._lock:
            return dict(self._services)

    def discover(self, name: str) -> object:
        with self._lock:
            service = self._services.get(name)
        if service is None:
            raise UnknownBootstrapServiceError(name)
        return service

    def register_api(self) -> bool:
        """
        Confirm every plugin endpoint is mounted under "/plugins".

        There is no separate route-registration step to perform here: every
        endpoint from commits 1-12 is already registered, at import time, by
        each module's own @router.* decorators. This is a verification that
        every router shares the expected prefix, not a second, redundant
        registration.
        """
        from .dashboard import router as plugin_dashboard_router
        from .event_system import router as event_system_router
        from .extension_api import router as extension_api_router
        from .plugin_analytics import router as plugin_analytics_router
        from .plugin_config import router as plugin_config_router
        from .plugin_dependencies import router as plugin_dependencies_router
        from .plugin_lifecycle import router as plugin_lifecycle_router
        from .plugin_loader import router as plugin_loader_router
        from .plugin_marketplace import router as plugin_marketplace_router
        from .plugin_packaging import router as plugin_packaging_router
        from .plugin_registry import router as plugin_registry_router
        from .plugin_sandbox import router as plugin_sandbox_router

        routers = (
            plugin_registry_router,
            plugin_loader_router,
            plugin_lifecycle_router,
            extension_api_router,
            event_system_router,
            plugin_dependencies_router,
            plugin_config_router,
            plugin_sandbox_router,
            plugin_marketplace_router,
            plugin_analytics_router,
            plugin_dashboard_router,
            plugin_packaging_router,
        )
        return all(router.prefix.startswith("/plugins") for router in routers)

    def discover_plugins(self, directory: Optional[str] = None) -> list:
        """Scan a directory for plugin manifests (*.plugin.json) without installing them."""
        loader = self.discover("plugin_loader")
        directory = directory or PLUGINS_DIR
        os.makedirs(directory, exist_ok=True)
        return loader.discover(directory)

    def install_discovered_plugins(self, directory: Optional[str] = None) -> list:
        """Discover manifests on disk and install any not already known to the lifecycle manager."""
        from .plugin_lifecycle import PluginAlreadyInstalledError

        lifecycle = self.discover("plugin_lifecycle_manager")
        installed = []
        for manifest in self.discover_plugins(directory):
            try:
                lifecycle.install(manifest, reason="startup-discovery")
            except PluginAlreadyInstalledError:
                continue
            installed.append(manifest.name)
        return installed

    def initialize_event_hooks(self) -> None:
        """Bridge the event bus into analytics: every PluginEnabled event records an Activation metric.

        Safe to call repeatedly, including from a fresh PluginBootstrap()
        instance: the HookEventSystem is a shared singleton, so idempotency
        is checked against its actual subscription state (any existing
        subscription owned by SUBSYSTEM_NAME) rather than private instance
        memory, which a new instance would never see.
        """
        from .event_system import EventType

        event_system = self.discover("hook_event_system")
        analytics = self.discover("plugin_analytics_service")

        existing = event_system.list_subscriptions(EventType.PLUGIN_ENABLED.value)
        for registration in existing:
            if registration.plugin == SUBSYSTEM_NAME:
                event_system.unsubscribe(registration.hook_id)

        def _record_activation(event) -> None:
            from .plugin_analytics import MetricType

            plugin = event.payload.get("plugin")
            if plugin:
                analytics.record(plugin, MetricType.ACTIVATION)

        event_system.subscribe(EventType.PLUGIN_ENABLED, SUBSYSTEM_NAME, _record_activation)

    def enable_startup_plugins(self) -> list:
        """Re-enable any plugin left in the Installed state, e.g. after discovery."""
        lifecycle = self.discover("plugin_lifecycle_manager")
        return lifecycle.auto_enable_installed()

    def validate(self, *, timestamp: Optional[datetime] = None) -> PluginBootstrapValidationResult:
        with self._lock:
            services = dict(self._services)
        if not services:
            services = self.register()

        missing = tuple(name for name in REQUIRED_SERVICES if services.get(name) is None)
        result = PluginBootstrapValidationResult(
            valid=not missing,
            registered_services=tuple(sorted(services)),
            missing_services=missing,
            checked_at=timestamp or datetime.now(timezone.utc),
        )
        if not result.valid:
            raise PluginBootstrapError(result)
        return result

    def health_check(self) -> dict:
        with self._lock:
            dashboard = self._services.get("plugin_dashboard_api")
            registered = tuple(sorted(self._services))
        if dashboard is None:
            raise PluginBootstrapError(
                PluginBootstrapValidationResult(
                    valid=False,
                    registered_services=registered,
                    missing_services=("plugin_dashboard_api",),
                )
            )
        return {"status": "ok", **dashboard.overview()}


_bootstrap = PluginBootstrap()


def get_plugin_bootstrap() -> PluginBootstrap:
    return _bootstrap


def bootstrap_plugin_framework(*, plugins_dir: Optional[str] = None) -> PluginBootstrapValidationResult:
    """Wire, validate, and activate the full Plugin & Extension Framework.

    Safe to call more than once: each call re-registers the current
    singletons, re-wires event hooks (idempotently), re-discovers and
    installs any new manifests found on disk, re-enables any plugin left
    Installed, and re-runs validation.
    """
    bootstrap = get_plugin_bootstrap()
    bootstrap.register()
    bootstrap.initialize_event_hooks()
    bootstrap.install_discovered_plugins(plugins_dir)
    bootstrap.enable_startup_plugins()
    return bootstrap.validate()
