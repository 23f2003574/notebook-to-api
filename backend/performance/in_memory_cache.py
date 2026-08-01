from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Optional


@dataclass(frozen=True)
class CacheNode:
    """A single slot in the in-memory cache."""

    key: str
    value: Any
    created_at: datetime
    expires_at: Optional[datetime]
    size_bytes: int

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(timezone.utc)) >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class MemoryCacheStats:
    """A snapshot of in-memory cache usage."""

    entries: int
    memory_bytes: int
    hits: int
    misses: int

    def to_dict(self) -> dict:
        return {
            "entries": self.entries,
            "memory_bytes": self.memory_bytes,
            "hits": self.hits,
            "misses": self.misses,
        }


class InMemoryCache:
    """A thread-safe, dict-backed cache offering O(1) key lookup with TTL expiration."""

    def __init__(self) -> None:
        self._nodes: dict = {}
        self._lock = RLock()
        self._hits = 0
        self._misses = 0

    def set(self, key: str, value: Any, *, ttl_seconds: Optional[float] = None) -> CacheNode:
        if not key:
            raise ValueError("key is required")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = datetime.now(timezone.utc)
        node = CacheNode(
            key=key,
            value=value,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds) if ttl_seconds else None,
            size_bytes=sys.getsizeof(value),
        )
        with self._lock:
            self._nodes[key] = node
        return node

    def get(self, key: str) -> CacheNode:
        with self._lock:
            node = self._nodes.get(key)
            if node is None:
                self._misses += 1
                raise KeyError(key)
            if node.is_expired():
                del self._nodes[key]
                self._misses += 1
                raise KeyError(key)
            self._hits += 1
            return node

    def contains(self, key: str) -> bool:
        with self._lock:
            node = self._nodes.get(key)
            if node is None:
                return False
            if node.is_expired():
                del self._nodes[key]
                return False
            return True

    def remove(self, key: str) -> None:
        with self._lock:
            if key not in self._nodes:
                raise KeyError(key)
            del self._nodes[key]

    def clear(self) -> int:
        with self._lock:
            count = len(self._nodes)
            self._nodes.clear()
            return count

    def stats(self) -> MemoryCacheStats:
        with self._lock:
            now = datetime.now(timezone.utc)
            live = [node for node in self._nodes.values() if not node.is_expired(now=now)]
            return MemoryCacheStats(
                entries=len(live),
                memory_bytes=sum(node.size_bytes for node in live),
                hits=self._hits,
                misses=self._misses,
            )


_in_memory_cache = InMemoryCache()


def get_in_memory_cache() -> InMemoryCache:
    return _in_memory_cache
