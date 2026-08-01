from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Depends

from .in_memory_cache import InMemoryCache, get_in_memory_cache
from .distributed_cache import (
    CacheBackend,
    ConnectionConfig,
    DistributedCacheAdapter,
    UnknownBackendError,
    get_distributed_cache_adapter,
)
from .cache_eviction import (
    CacheEvictionEngine,
    EvictionPolicy,
    get_cache_eviction_engine,
)
from .response_cache import ResponseCacheMiddleware, get_response_cache_middleware
from .cache_invalidation import (
    CacheInvalidationService,
    InvalidationTrigger,
    get_cache_invalidation_service,
)


class CacheKeyError(KeyError):
    pass


@dataclass(frozen=True)
class CacheEntry:
    """A single stored value along with its expiry metadata."""

    key: str
    value: Any
    namespace: str
    created_at: datetime
    expires_at: Optional[datetime]

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(timezone.utc)) >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "namespace": self.namespace,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass(frozen=True)
class CacheStats:
    """A snapshot of cache usage counters."""

    size: int
    hits: int
    misses: int
    evictions: int

    def to_dict(self) -> dict:
        return {
            "size": self.size,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }


def _namespaced(namespace: str, key: str) -> str:
    return f"{namespace}:{key}"


class CacheManager:
    """An in-memory, namespace-isolated key-value cache with TTL support."""

    def __init__(self) -> None:
        self._entries: dict = {}
        self._lock = Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def put(
        self,
        key: str,
        value: Any,
        *,
        namespace: str = "default",
        ttl_seconds: Optional[float] = None,
    ) -> CacheEntry:
        if not key:
            raise ValueError("key is required")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = datetime.now(timezone.utc)
        entry = CacheEntry(
            key=key,
            value=value,
            namespace=namespace,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds) if ttl_seconds else None,
        )
        with self._lock:
            self._entries[_namespaced(namespace, key)] = entry
        return entry

    def get(self, key: str, *, namespace: str = "default") -> CacheEntry:
        full_key = _namespaced(namespace, key)
        with self._lock:
            entry = self._entries.get(full_key)
            if entry is None:
                self._misses += 1
                raise CacheKeyError(key)
            if entry.is_expired():
                del self._entries[full_key]
                self._misses += 1
                self._evictions += 1
                raise CacheKeyError(key)
            self._hits += 1
            return entry

    def delete(self, key: str, *, namespace: str = "default") -> None:
        full_key = _namespaced(namespace, key)
        with self._lock:
            if full_key not in self._entries:
                raise CacheKeyError(key)
            del self._entries[full_key]

    def clear(self, *, namespace: Optional[str] = None) -> int:
        with self._lock:
            if namespace is None:
                count = len(self._entries)
                self._entries.clear()
                return count
            prefix = f"{namespace}:"
            keys = [k for k in self._entries if k.startswith(prefix)]
            for k in keys:
                del self._entries[k]
            return len(keys)

    def stats(self) -> CacheStats:
        with self._lock:
            now = datetime.now(timezone.utc)
            live = sum(1 for entry in self._entries.values() if not entry.is_expired(now=now))
            return CacheStats(
                size=live,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
            )

    def keys(self, namespace: Optional[str] = None) -> list:
        with self._lock:
            if namespace is None:
                return [entry.key for entry in self._entries.values()]
            prefix = f"{namespace}:"
            return [k[len(prefix):] for k in self._entries if k.startswith(prefix)]


_cache_manager = CacheManager()


def get_cache_manager() -> CacheManager:
    return _cache_manager


router = APIRouter(prefix="/performance/cache", tags=["cache-manager"])


@router.post("", status_code=201)
def put_cache_endpoint(
    payload: dict = Body(default={}),
    cache: CacheManager = Depends(get_cache_manager),
) -> dict:
    try:
        entry = cache.put(
            payload.get("key", ""),
            payload.get("value"),
            namespace=payload.get("namespace", "default"),
            ttl_seconds=payload.get("ttl_seconds"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return entry.to_dict()


@router.get("/backend")
def list_backends_endpoint(
    adapter: DistributedCacheAdapter = Depends(get_distributed_cache_adapter),
) -> dict:
    return {"backends": adapter.list_backends()}


@router.get("/eviction/stats")
def eviction_stats_endpoint(
    engine: CacheEvictionEngine = Depends(get_cache_eviction_engine),
) -> dict:
    return engine.stats()


@router.get("/eviction")
def get_eviction_config_endpoint(
    engine: CacheEvictionEngine = Depends(get_cache_eviction_engine),
) -> dict:
    return engine.config()


@router.get("/responses")
def list_cached_responses_endpoint(
    response_cache: ResponseCacheMiddleware = Depends(get_response_cache_middleware),
) -> dict:
    stats = response_cache.stats()
    return {
        "responses": [cached.to_dict() for cached in response_cache.list_cached()],
        **stats,
    }


@router.delete("/responses", status_code=204)
def clear_cached_responses_endpoint(
    response_cache: ResponseCacheMiddleware = Depends(get_response_cache_middleware),
) -> None:
    response_cache.invalidate()


@router.get("/{key}")
def get_cache_endpoint(
    key: str,
    namespace: str = "default",
    cache: CacheManager = Depends(get_cache_manager),
) -> dict:
    try:
        entry = cache.get(key, namespace=namespace)
    except CacheKeyError:
        raise HTTPException(status_code=404, detail="key not found")
    return entry.to_dict()


@router.delete("/{key}", status_code=204)
def delete_cache_endpoint(
    key: str,
    namespace: str = "default",
    cache: CacheManager = Depends(get_cache_manager),
) -> None:
    try:
        cache.delete(key, namespace=namespace)
    except CacheKeyError:
        raise HTTPException(status_code=404, detail="key not found")


@router.delete("", status_code=204)
def clear_cache_endpoint(
    namespace: Optional[str] = None,
    cache: CacheManager = Depends(get_cache_manager),
) -> None:
    cache.clear(namespace=namespace)


@router.get("/memory/stats")
def get_memory_cache_stats_endpoint(
    memory_cache: InMemoryCache = Depends(get_in_memory_cache),
) -> dict:
    return memory_cache.stats().to_dict()


@router.post("/memory/clear", status_code=204)
def clear_memory_cache_endpoint(
    memory_cache: InMemoryCache = Depends(get_in_memory_cache),
) -> None:
    memory_cache.clear()


@router.post("/backend", status_code=201)
def connect_backend_endpoint(
    payload: dict = Body(default={}),
    adapter: DistributedCacheAdapter = Depends(get_distributed_cache_adapter),
) -> dict:
    try:
        backend = CacheBackend(payload.get("backend", ""))
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown backend type")
    config = ConnectionConfig(
        name=payload.get("name", ""),
        backend=backend,
        host=payload.get("host", "localhost"),
        port=payload.get("port", 6379),
        timeout_seconds=payload.get("timeout_seconds", 5.0),
        priority=payload.get("priority", 0),
        extra=payload.get("extra", {}),
    )
    if not config.name:
        raise HTTPException(status_code=422, detail="name is required")
    connection = adapter.connect(config)
    return connection.to_dict()


@router.get("/backend/health")
def backend_health_endpoint(
    adapter: DistributedCacheAdapter = Depends(get_distributed_cache_adapter),
) -> dict:
    return {"backends": adapter.health_check()}


@router.post("/eviction", status_code=201)
def configure_eviction_endpoint(
    payload: dict = Body(default={}),
    engine: CacheEvictionEngine = Depends(get_cache_eviction_engine),
    memory_cache: InMemoryCache = Depends(get_in_memory_cache),
) -> dict:
    try:
        policy = EvictionPolicy(payload.get("policy", EvictionPolicy.LRU.value))
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown eviction policy")
    try:
        engine.configure(
            policy=policy,
            max_entries=payload.get("max_entries"),
            max_memory_bytes=payload.get("max_memory_bytes"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    result = engine.evict(memory_cache)
    return result.to_dict()


@router.post("/responses/invalidate")
def invalidate_cached_responses_endpoint(
    payload: dict = Body(default={}),
    response_cache: ResponseCacheMiddleware = Depends(get_response_cache_middleware),
) -> dict:
    invalidated = response_cache.invalidate(
        key=payload.get("key"),
        path=payload.get("path"),
    )
    return {"invalidated": invalidated}


def _parse_trigger(payload: dict) -> InvalidationTrigger:
    try:
        return InvalidationTrigger(payload.get("trigger", InvalidationTrigger.MANUAL.value))
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown invalidation trigger")


@router.post("/invalidate")
def invalidate_key_endpoint(
    payload: dict = Body(default={}),
    service: CacheInvalidationService = Depends(get_cache_invalidation_service),
) -> dict:
    key = payload.get("key", "")
    trigger = _parse_trigger(payload)
    try:
        result = service.invalidate(key, namespace=payload.get("namespace", "default"), trigger=trigger)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.post("/invalidate-pattern")
def invalidate_pattern_endpoint(
    payload: dict = Body(default={}),
    service: CacheInvalidationService = Depends(get_cache_invalidation_service),
) -> dict:
    pattern = payload.get("pattern", "")
    trigger = _parse_trigger(payload)
    try:
        result = service.invalidate_pattern(
            pattern, namespace=payload.get("namespace", "default"), trigger=trigger
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result.to_dict()


@router.delete("/namespace/{namespace}")
def invalidate_namespace_endpoint(
    namespace: str,
    service: CacheInvalidationService = Depends(get_cache_invalidation_service),
) -> dict:
    result = service.invalidate_namespace(namespace)
    return result.to_dict()
