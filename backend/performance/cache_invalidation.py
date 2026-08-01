from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from threading import Lock
from typing import TYPE_CHECKING, Optional

from .distributed_cache import DistributedCacheAdapter, NoAvailableBackendError

if TYPE_CHECKING:
    from .cache_manager import CacheManager
    from .in_memory_cache import InMemoryCache


class InvalidationTrigger(str, Enum):
    """What caused a cache entry to be invalidated."""

    TTL_EXPIRED = "ttl_expired"
    DATA_UPDATED = "data_updated"
    MANUAL = "manual"
    NAMESPACE_FLUSH = "namespace_flush"


@dataclass(frozen=True)
class InvalidationRequest:
    """Describes a single invalidation operation."""

    trigger: InvalidationTrigger = InvalidationTrigger.MANUAL
    key: Optional[str] = None
    pattern: Optional[str] = None
    namespace: str = "default"

    def to_dict(self) -> dict:
        return {
            "trigger": self.trigger.value,
            "key": self.key,
            "pattern": self.pattern,
            "namespace": self.namespace,
        }


@dataclass(frozen=True)
class InvalidationResult:
    """The outcome of an invalidation operation."""

    trigger: InvalidationTrigger
    keys_invalidated: list = field(default_factory=list)
    count: int = 0
    propagated: bool = False
    backends_notified: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "trigger": self.trigger.value,
            "keys_invalidated": self.keys_invalidated,
            "count": self.count,
            "propagated": self.propagated,
            "backends_notified": self.backends_notified,
        }


class CacheInvalidationService:
    """Centralizes cache invalidation across local caches, with best-effort propagation
    to the connected distributed backend."""

    def __init__(
        self,
        *,
        cache_manager: CacheManager,
        memory_cache: InMemoryCache,
        distributed_adapter: Optional[DistributedCacheAdapter] = None,
    ) -> None:
        self._cache_manager = cache_manager
        self._memory_cache = memory_cache
        self._distributed = distributed_adapter
        self._lock = Lock()
        self._total_invalidations = 0
        self._counts_by_trigger: dict = {trigger: 0 for trigger in InvalidationTrigger}

    def _propagate_delete(self, keys: list) -> tuple:
        if self._distributed is None or not keys:
            return False, []
        propagated = False
        for key in keys:
            try:
                self._distributed.delete(key)
                propagated = True
            except (KeyError, NoAvailableBackendError):
                continue
        if not propagated:
            return False, []
        name = self._distributed.active_backend_name()
        return True, [name] if name else []

    def _record(self, trigger: InvalidationTrigger, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            self._total_invalidations += count
            self._counts_by_trigger[trigger] += count

    def invalidate(
        self,
        key: str,
        *,
        namespace: str = "default",
        trigger: InvalidationTrigger = InvalidationTrigger.MANUAL,
    ) -> InvalidationResult:
        if not key:
            raise ValueError("key is required")
        removed = False
        try:
            self._cache_manager.delete(key, namespace=namespace)
            removed = True
        except KeyError:
            pass
        try:
            self._memory_cache.remove(key)
            removed = True
        except KeyError:
            pass
        propagated, backends = self._propagate_delete([key])
        count = 1 if (removed or propagated) else 0
        self._record(trigger, count)
        return InvalidationResult(
            trigger=trigger,
            keys_invalidated=[key] if count else [],
            count=count,
            propagated=propagated,
            backends_notified=backends,
        )

    def invalidate_pattern(
        self,
        pattern: str,
        *,
        namespace: str = "default",
        trigger: InvalidationTrigger = InvalidationTrigger.MANUAL,
    ) -> InvalidationResult:
        if not pattern:
            raise ValueError("pattern is required")
        matched = {key for key in self._cache_manager.keys(namespace) if fnmatch(key, pattern)}
        matched |= {key for key, _ in self._memory_cache.items() if fnmatch(key, pattern)}

        removed = []
        for key in sorted(matched):
            hit = False
            try:
                self._cache_manager.delete(key, namespace=namespace)
                hit = True
            except KeyError:
                pass
            try:
                self._memory_cache.remove(key)
                hit = True
            except KeyError:
                pass
            if hit:
                removed.append(key)

        propagated, backends = self._propagate_delete(removed)
        count = len(removed)
        self._record(trigger, count)
        return InvalidationResult(
            trigger=trigger,
            keys_invalidated=removed,
            count=count,
            propagated=propagated,
            backends_notified=backends,
        )

    def invalidate_namespace(
        self,
        namespace: str,
        *,
        trigger: InvalidationTrigger = InvalidationTrigger.NAMESPACE_FLUSH,
    ) -> InvalidationResult:
        if not namespace:
            raise ValueError("namespace is required")
        keys = self._cache_manager.keys(namespace)
        count = self._cache_manager.clear(namespace=namespace)
        propagated, backends = self._propagate_delete(keys)
        self._record(trigger, count)
        return InvalidationResult(
            trigger=trigger,
            keys_invalidated=sorted(keys),
            count=count,
            propagated=propagated,
            backends_notified=backends,
        )

    def invalidate_all(
        self,
        *,
        trigger: InvalidationTrigger = InvalidationTrigger.MANUAL,
    ) -> InvalidationResult:
        manager_count = self._cache_manager.clear()
        memory_count = self._memory_cache.clear()
        count = manager_count + memory_count
        propagated = False
        backends: list = []
        if self._distributed is not None:
            try:
                self._distributed.clear()
                propagated = True
                name = self._distributed.active_backend_name()
                if name:
                    backends.append(name)
            except NoAvailableBackendError:
                pass
        self._record(trigger, count)
        return InvalidationResult(
            trigger=trigger,
            keys_invalidated=[],
            count=count,
            propagated=propagated,
            backends_notified=backends,
        )

    def stats(self) -> dict:
        with self._lock:
            return {
                "total_invalidations": self._total_invalidations,
                "by_trigger": {
                    trigger.value: count for trigger, count in self._counts_by_trigger.items()
                },
            }


_cache_invalidation_service: Optional[CacheInvalidationService] = None


def get_cache_invalidation_service() -> CacheInvalidationService:
    global _cache_invalidation_service
    if _cache_invalidation_service is None:
        from .cache_manager import get_cache_manager
        from .in_memory_cache import get_in_memory_cache
        from .distributed_cache import get_distributed_cache_adapter

        _cache_invalidation_service = CacheInvalidationService(
            cache_manager=get_cache_manager(),
            memory_cache=get_in_memory_cache(),
            distributed_adapter=get_distributed_cache_adapter(),
        )
    return _cache_invalidation_service
