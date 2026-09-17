from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Optional


@dataclass
class CacheContext:
    """Request/response state threaded through the response cache for a single call."""

    method: str
    path: str
    query_params: dict = field(default_factory=dict)
    status_code: Optional[int] = None
    body: Any = None
    headers: dict = field(default_factory=dict)
    ttl_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "path": self.path,
            "query_params": self.query_params,
            "status_code": self.status_code,
            "body": self.body,
            "headers": self.headers,
            "ttl_seconds": self.ttl_seconds,
        }


@dataclass(frozen=True)
class CachedResponse:
    """A single cached HTTP response."""

    key: str
    status_code: int
    body: Any
    headers: dict
    created_at: datetime
    expires_at: Optional[datetime]

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(timezone.utc)) >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "status_code": self.status_code,
            "body": self.body,
            "headers": self.headers,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


class ResponseCacheMiddleware:
    """Caches GET responses by request identity, with TTL expiration and invalidation."""

    def __init__(self, *, default_ttl_seconds: Optional[float] = None) -> None:
        self.default_ttl_seconds = default_ttl_seconds
        self._entries: dict = {}
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def cache_key(self, method: str, path: str, query_params: Optional[dict] = None) -> str:
        normalized = "&".join(f"{k}={v}" for k, v in sorted((query_params or {}).items()))
        suffix = f"?{normalized}" if normalized else ""
        return f"{method.upper()}:{path}{suffix}"

    def before_request(self, context: CacheContext) -> Optional[CachedResponse]:
        if context.method.upper() != "GET":
            return None
        key = self.cache_key(context.method, context.path, context.query_params)
        with self._lock:
            cached = self._entries.get(key)
            if cached is None:
                self._misses += 1
                return None
            if cached.is_expired():
                del self._entries[key]
                self._misses += 1
                return None
            self._hits += 1
            return cached

    def after_response(self, context: CacheContext) -> Optional[CachedResponse]:
        if context.method.upper() != "GET":
            return None
        if context.status_code is not None and context.status_code >= 400:
            return None
        key = self.cache_key(context.method, context.path, context.query_params)
        now = datetime.now(timezone.utc)
        ttl = context.ttl_seconds if context.ttl_seconds is not None else self.default_ttl_seconds
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        cached = CachedResponse(
            key=key,
            status_code=context.status_code or 200,
            body=context.body,
            headers=dict(context.headers),
            created_at=now,
            expires_at=now + timedelta(seconds=ttl) if ttl else None,
        )
        with self._lock:
            self._entries[key] = cached
        return cached

    def invalidate(self, *, key: Optional[str] = None, path: Optional[str] = None) -> int:
        with self._lock:
            if key is not None:
                if key in self._entries:
                    del self._entries[key]
                    return 1
                return 0
            if path is not None:
                matches = [k for k in self._entries if k.split(":", 1)[1].split("?")[0] == path]
                for k in matches:
                    del self._entries[k]
                return len(matches)
            count = len(self._entries)
            self._entries.clear()
            return count

    def list_cached(self) -> list:
        with self._lock:
            return list(self._entries.values())

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
            }


_response_cache_middleware = ResponseCacheMiddleware()


def get_response_cache_middleware() -> ResponseCacheMiddleware:
    return _response_cache_middleware
