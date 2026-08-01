from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Optional


class CacheBackend(str, Enum):
    """The kind of external cache service a connection targets."""

    REDIS = "redis"
    MEMCACHED = "memcached"
    CUSTOM = "custom"


class NoAvailableBackendError(RuntimeError):
    pass


class UnknownBackendError(KeyError):
    pass


@dataclass(frozen=True)
class ConnectionConfig:
    """Describes how to reach a single cache backend."""

    name: str
    backend: CacheBackend
    host: str = "localhost"
    port: int = 6379
    timeout_seconds: float = 5.0
    priority: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "backend": self.backend.value,
            "host": self.host,
            "port": self.port,
            "timeout_seconds": self.timeout_seconds,
            "priority": self.priority,
            "extra": self.extra,
        }


@dataclass
class _BackendConnection:
    config: ConnectionConfig
    connected: bool = False
    healthy: bool = True
    store: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            **self.config.to_dict(),
            "connected": self.connected,
            "healthy": self.healthy,
        }


class DistributedCacheAdapter:
    """A pluggable adapter that fronts one or more external cache backends with failover."""

    def __init__(self) -> None:
        self._connections: dict = {}
        self._lock = RLock()

    def connect(self, config: ConnectionConfig) -> _BackendConnection:
        with self._lock:
            connection = _BackendConnection(config=config, connected=True, healthy=True)
            self._connections[config.name] = connection
            return connection

    def _ordered_connections(self) -> list:
        return sorted(self._connections.values(), key=lambda c: c.config.priority)

    def _active_connection(self) -> _BackendConnection:
        for connection in self._ordered_connections():
            if connection.connected and connection.healthy:
                return connection
        raise NoAvailableBackendError("no healthy backend is available")

    def set(self, key: str, value: Any) -> str:
        if not key:
            raise ValueError("key is required")
        connection = self._active_connection()
        connection.store[key] = value
        return connection.config.name

    def get(self, key: str) -> Any:
        connection = self._active_connection()
        if key not in connection.store:
            raise KeyError(key)
        return connection.store[key]

    def delete(self, key: str) -> None:
        connection = self._active_connection()
        if key not in connection.store:
            raise KeyError(key)
        del connection.store[key]

    def set_backend_health(self, name: str, *, healthy: bool) -> None:
        with self._lock:
            connection = self._connections.get(name)
            if connection is None:
                raise UnknownBackendError(name)
            connection.healthy = healthy

    def disconnect(self, name: str) -> None:
        with self._lock:
            connection = self._connections.get(name)
            if connection is None:
                raise UnknownBackendError(name)
            connection.connected = False

    def list_backends(self) -> list:
        return [connection.to_dict() for connection in self._ordered_connections()]

    def health_check(self) -> list:
        results = []
        for connection in self._ordered_connections():
            results.append(
                {
                    "name": connection.config.name,
                    "backend": connection.config.backend.value,
                    "connected": connection.connected,
                    "healthy": connection.connected and connection.healthy,
                }
            )
        return results


_distributed_cache_adapter = DistributedCacheAdapter()


def get_distributed_cache_adapter() -> DistributedCacheAdapter:
    return _distributed_cache_adapter
