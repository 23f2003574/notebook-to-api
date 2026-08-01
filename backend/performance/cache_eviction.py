from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Optional

from .in_memory_cache import InMemoryCache


class EvictionPolicy(str, Enum):
    """The strategy used to pick which entries to reclaim."""

    LRU = "lru"
    LFU = "lfu"
    FIFO = "fifo"
    TTL = "ttl"


@dataclass(frozen=True)
class EvictionResult:
    """The outcome of a single eviction pass."""

    policy: EvictionPolicy
    reason: str
    evicted_keys: list = field(default_factory=list)
    reclaimed_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "policy": self.policy.value,
            "reason": self.reason,
            "evicted_keys": self.evicted_keys,
            "reclaimed_bytes": self.reclaimed_bytes,
        }


def _sort_key(policy: EvictionPolicy):
    if policy is EvictionPolicy.LRU:
        return lambda item: item[1].last_accessed_at
    if policy is EvictionPolicy.LFU:
        return lambda item: (item[1].access_count, item[1].last_accessed_at)
    if policy is EvictionPolicy.FIFO:
        return lambda item: item[1].created_at
    return lambda item: (
        item[1].expires_at is None,
        item[1].expires_at or item[1].created_at,
    )


class CacheEvictionEngine:
    """Reclaims cache space by expiring and evicting entries under a configurable policy."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._policy = EvictionPolicy.LRU
        self._max_entries: Optional[int] = None
        self._max_memory_bytes: Optional[int] = None
        self._total_evictions = 0
        self._evictions_by_policy: dict = {policy: 0 for policy in EvictionPolicy}
        self._runs = 0

    def configure(
        self,
        *,
        policy: EvictionPolicy = EvictionPolicy.LRU,
        max_entries: Optional[int] = None,
        max_memory_bytes: Optional[int] = None,
    ) -> None:
        if max_entries is not None and max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if max_memory_bytes is not None and max_memory_bytes <= 0:
            raise ValueError("max_memory_bytes must be positive")
        with self._lock:
            self._policy = policy
            self._max_entries = max_entries
            self._max_memory_bytes = max_memory_bytes

    def config(self) -> dict:
        with self._lock:
            return {
                "policy": self._policy.value,
                "max_entries": self._max_entries,
                "max_memory_bytes": self._max_memory_bytes,
            }

    def select(self, cache: InMemoryCache, *, count: int = 1) -> list:
        if count <= 0:
            return []
        now = datetime.now(timezone.utc)
        live = [(key, node) for key, node in cache.items() if not node.is_expired(now=now)]
        live.sort(key=_sort_key(self._policy))
        return [key for key, _ in live[:count]]

    def evict(self, cache: InMemoryCache, *, count: Optional[int] = None) -> EvictionResult:
        with self._lock:
            policy = self._policy
            max_entries = self._max_entries
            max_memory_bytes = self._max_memory_bytes

        now = datetime.now(timezone.utc)
        items = cache.items()
        expired_keys = [key for key, node in items if node.is_expired(now=now)]
        sizes = {key: node.size_bytes for key, node in items}

        evicted: list = []
        reclaimed = 0
        for key in expired_keys:
            try:
                cache.remove(key)
            except KeyError:
                continue
            evicted.append(key)
            reclaimed += sizes.get(key, 0)

        reason = "ttl"

        if count is not None:
            reason = "manual"
            for key in self.select(cache, count=count):
                try:
                    cache.remove(key)
                except KeyError:
                    continue
                evicted.append(key)
                reclaimed += sizes.get(key, 0)
        elif max_entries is not None or max_memory_bytes is not None:
            stats = cache.stats()
            entries = stats.entries
            memory_bytes = stats.memory_bytes
            while (max_entries is not None and entries > max_entries) or (
                max_memory_bytes is not None and memory_bytes > max_memory_bytes
            ):
                candidates = self.select(cache, count=1)
                if not candidates:
                    break
                key = candidates[0]
                size = next((n.size_bytes for k, n in cache.items() if k == key), 0)
                try:
                    cache.remove(key)
                except KeyError:
                    break
                evicted.append(key)
                reclaimed += size
                entries -= 1
                memory_bytes -= size
            reason = "threshold"

        with self._lock:
            self._runs += 1
            if evicted:
                self._total_evictions += len(evicted)
                self._evictions_by_policy[policy] += len(evicted)

        return EvictionResult(
            policy=policy,
            reason=reason,
            evicted_keys=evicted,
            reclaimed_bytes=reclaimed,
        )

    def stats(self) -> dict:
        with self._lock:
            return {
                "total_evictions": self._total_evictions,
                "evictions_by_policy": {
                    policy.value: count for policy, count in self._evictions_by_policy.items()
                },
                "runs": self._runs,
                "policy": self._policy.value,
                "max_entries": self._max_entries,
                "max_memory_bytes": self._max_memory_bytes,
            }


_cache_eviction_engine = CacheEvictionEngine()


def get_cache_eviction_engine() -> CacheEvictionEngine:
    return _cache_eviction_engine
