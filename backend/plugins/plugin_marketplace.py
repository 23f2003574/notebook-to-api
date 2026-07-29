from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .extension_api import API_VERSION, IncompatibleApiVersionError, is_compatible_version
from .plugin_dependencies import _compare_versions, _parse_version
from .plugin_lifecycle import (
    PluginAlreadyInstalledError,
    PluginLifecycleManager,
    PluginState,
    get_plugin_lifecycle_manager,
)
from .plugin_loader import PluginManifest
from .plugin_registry import PluginRegistry, UnknownPluginError, get_plugin_registry


class UntrustedSourceError(ValueError):
    pass


class UnknownMarketplacePluginError(KeyError):
    pass


class MarketplacePluginAlreadyListedError(ValueError):
    pass


class NoUpdateAvailableError(ValueError):
    pass


@dataclass(frozen=True)
class MarketplaceSource:
    """A catalog source a marketplace listing is published from."""

    name: str
    url: str
    trusted: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "url": self.url, "trusted": self.trusted}


@dataclass(frozen=True)
class MarketplacePlugin:
    """A single version of a plugin as listed in the marketplace catalog."""

    name: str
    version: str
    entry_point: str
    source: MarketplaceSource
    description: str = ""
    author: str = ""
    tags: tuple = ()
    min_api_version: Optional[str] = None
    featured: bool = False

    def matches(self, query: Optional[str]) -> bool:
        if not query:
            return True
        query_lower = query.lower()
        return query_lower in self.name.lower() or query_lower in self.description.lower()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "entry_point": self.entry_point,
            "source": self.source.to_dict(),
            "description": self.description,
            "author": self.author,
            "tags": list(self.tags),
            "min_api_version": self.min_api_version,
            "featured": self.featured,
        }


class PluginMarketplaceService:
    """A searchable catalog of installable plugins, bridging into the real install path."""

    def __init__(
        self,
        lifecycle: Optional[PluginLifecycleManager] = None,
        registry: Optional[PluginRegistry] = None,
    ) -> None:
        self._lifecycle = lifecycle if lifecycle is not None else get_plugin_lifecycle_manager()
        self._registry = registry if registry is not None else get_plugin_registry()
        self._listings: dict = {}
        self._lock = Lock()

    def list_plugin(self, plugin: MarketplacePlugin) -> MarketplacePlugin:
        """Publish a listing to the catalog. Not exposed over HTTP in this commit -
        catalog seeding is expected to come from a trusted ingestion path, not
        arbitrary API callers."""
        with self._lock:
            versions = self._listings.setdefault(plugin.name, {})
            if plugin.version in versions:
                raise MarketplacePluginAlreadyListedError(f"{plugin.name}@{plugin.version}")
            versions[plugin.version] = plugin
        return plugin

    def _latest(self, name: str) -> MarketplacePlugin:
        with self._lock:
            versions = self._listings.get(name)
        if not versions:
            raise UnknownMarketplacePluginError(name)
        return max(versions.values(), key=lambda listing: _parse_version(listing.version))

    def _all_latest(self) -> list:
        with self._lock:
            names = list(self._listings)
        return [self._latest(name) for name in names]

    def search(self, query: Optional[str] = None, tags: Optional[list] = None) -> list:
        results = [listing for listing in self._all_latest() if listing.matches(query)]
        if tags:
            tag_set = set(tags)
            results = [listing for listing in results if tag_set & set(listing.tags)]
        return sorted(results, key=lambda listing: listing.name)

    def featured(self) -> list:
        return sorted((listing for listing in self._all_latest() if listing.featured), key=lambda listing: listing.name)

    def _check_installable(self, listing: MarketplacePlugin, allow_untrusted: bool) -> None:
        if not listing.source.trusted and not allow_untrusted:
            raise UntrustedSourceError(
                f"source '{listing.source.name}' is not trusted; pass allow_untrusted=True to override"
            )
        if listing.min_api_version is not None and not is_compatible_version(listing.min_api_version):
            raise IncompatibleApiVersionError(
                f"'{listing.name}' requires extension api version '{listing.min_api_version}', "
                f"incompatible with host version '{API_VERSION}'"
            )

    def install(self, name: str, version: Optional[str] = None, *, allow_untrusted: bool = False):
        with self._lock:
            versions = self._listings.get(name)
        if not versions:
            raise UnknownMarketplacePluginError(name)
        if version is None:
            listing = self._latest(name)
        else:
            listing = versions.get(version)
            if listing is None:
                raise UnknownMarketplacePluginError(f"{name}@{version}")

        self._check_installable(listing, allow_untrusted)

        manifest = PluginManifest(
            name=listing.name,
            version=listing.version,
            entry_point=listing.entry_point,
            description=listing.description,
            author=listing.author,
            tags=listing.tags,
        )
        return self._lifecycle.install(manifest, reason=f"marketplace:{listing.source.name}")

    def update(self, name: str, *, allow_untrusted: bool = False):
        """Install the latest compatible marketplace version over an installed plugin.

        If the plugin is currently enabled, it is uninstalled (unloaded) and
        then reinstalled at the new version, ending in the Installed state -
        it is deliberately NOT auto-re-enabled, so newly-updated code doesn't
        start running without an explicit enable() call.
        """
        current_state = self._lifecycle.get_state(name)
        current_plugin = self._registry.get(name)
        latest = self._latest(name)

        if _compare_versions(latest.version, current_plugin.version) <= 0:
            raise NoUpdateAvailableError(
                f"'{name}' is already up to date at version '{current_plugin.version}'"
            )

        self._check_installable(latest, allow_untrusted)

        if current_state != PluginState.UNINSTALLED:
            self._lifecycle.uninstall(name, reason="marketplace-update")

        manifest = PluginManifest(
            name=latest.name,
            version=latest.version,
            entry_point=latest.entry_point,
            description=latest.description,
            author=latest.author,
            tags=latest.tags,
        )
        return self._lifecycle.install(manifest, reason=f"marketplace-update:{latest.source.name}")


_plugin_marketplace_service = PluginMarketplaceService()


def get_plugin_marketplace_service() -> PluginMarketplaceService:
    return _plugin_marketplace_service


router = APIRouter(prefix="/plugins/marketplace", tags=["plugins-marketplace"])


@router.get("")
def browse_marketplace_endpoint(
    featured: bool = Query(default=False),
    service: PluginMarketplaceService = Depends(get_plugin_marketplace_service),
) -> list:
    listings = service.featured() if featured else service.search()
    return [listing.to_dict() for listing in listings]


@router.get("/search")
def search_marketplace_endpoint(
    q: Optional[str] = Query(default=None),
    tag: Optional[list] = Query(default=None),
    service: PluginMarketplaceService = Depends(get_plugin_marketplace_service),
) -> list:
    return [listing.to_dict() for listing in service.search(query=q, tags=tag)]


@router.post("/install", status_code=201)
def install_marketplace_plugin_endpoint(
    payload: dict = Body(default={}),
    service: PluginMarketplaceService = Depends(get_plugin_marketplace_service),
) -> dict:
    plugin = payload.get("plugin", "")
    if not plugin:
        raise HTTPException(status_code=422, detail="plugin is required")
    try:
        event = service.install(
            plugin, payload.get("version"), allow_untrusted=payload.get("allow_untrusted", False)
        )
    except UnknownMarketplacePluginError:
        raise HTTPException(status_code=404, detail="plugin not found in marketplace")
    except UntrustedSourceError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except IncompatibleApiVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except PluginAlreadyInstalledError as exc:
        raise HTTPException(status_code=409, detail=f"'{exc}' is already installed")
    return {"plugin": plugin, "state": event.to_state.value}


@router.post("/update/{plugin}")
def update_marketplace_plugin_endpoint(
    plugin: str,
    payload: dict = Body(default={}),
    service: PluginMarketplaceService = Depends(get_plugin_marketplace_service),
) -> dict:
    try:
        event = service.update(plugin, allow_untrusted=payload.get("allow_untrusted", False))
    except UnknownPluginError:
        raise HTTPException(status_code=404, detail="plugin is not installed")
    except UnknownMarketplacePluginError:
        raise HTTPException(status_code=404, detail="plugin not found in marketplace")
    except NoUpdateAvailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UntrustedSourceError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except IncompatibleApiVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"plugin": plugin, "state": event.to_state.value}
