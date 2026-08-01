from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Depends

from .in_memory_cache import InMemoryCache, get_in_memory_cache


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
