from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Callable, Optional


class PoolType(str, Enum):
    """The kind of runtime object a pool manages."""

    WORKER = "worker"
    THREAD = "thread"
    BUFFER = "buffer"
    CUSTOM = "custom"


class UnknownPoolError(KeyError):
    pass


class PoolAlreadyExistsError(ValueError):
    pass


class PoolExhaustedError(RuntimeError):
    pass


class UnknownResourceError(KeyError):
    pass


class ResourceNotAcquiredError(ValueError):
    pass


@dataclass
class PooledResource:
    """A single reusable object managed by a pool."""

    resource_id: str
    pool_name: str
    created_at: datetime
    last_used_at: datetime
    in_use: bool = False
    use_count: int = 0
    payload: Any = None

    def to_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "pool_name": self.pool_name,
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat(),
            "in_use": self.in_use,
            "use_count": self.use_count,
        }


@dataclass
class ResourcePool:
    """Configuration and identity of a named resource pool."""

    name: str
    pool_type: PoolType
    min_size: int
    max_size: int
    idle_timeout_seconds: Optional[float] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "pool_type": self.pool_type.value,
            "min_size": self.min_size,
            "max_size": self.max_size,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "created_at": self.created_at.isoformat(),
        }


class ResourcePoolManager:
    """Manages pools of reusable runtime objects, acquired and released by callers."""

    def __init__(self) -> None:
        self._pools: dict = {}
        self._resources: dict = {}
        self._factories: dict = {}
        self._sequence: dict = {}
        self._lock = Lock()

    def create_pool(
        self,
        name: str,
        *,
        pool_type: PoolType,
        min_size: int = 0,
        max_size: int,
        idle_timeout_seconds: Optional[float] = None,
        factory: Optional[Callable[[], Any]] = None,
    ) -> ResourcePool:
        if not name:
            raise ValueError("name is required")
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        if min_size < 0:
            raise ValueError("min_size must not be negative")
        if min_size > max_size:
            raise ValueError("min_size must not exceed max_size")
        if idle_timeout_seconds is not None and idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        with self._lock:
            if name in self._pools:
                raise PoolAlreadyExistsError(name)
            pool = ResourcePool(
                name=name,
                pool_type=pool_type,
                min_size=min_size,
                max_size=max_size,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            self._pools[name] = pool
            self._resources[name] = {}
            self._factories[name] = factory or (lambda: None)
            self._sequence[name] = 0
            for _ in range(min_size):
                self._create_resource(name)
            return pool

    def _create_resource(self, pool_name: str) -> PooledResource:
        self._sequence[pool_name] += 1
        now = datetime.now(timezone.utc)
        resource = PooledResource(
            resource_id=f"{pool_name}-{self._sequence[pool_name]}",
            pool_name=pool_name,
            created_at=now,
            last_used_at=now,
            payload=self._factories[pool_name](),
        )
        self._resources[pool_name][resource.resource_id] = resource
        return resource

    def acquire(self, pool_name: str) -> PooledResource:
        with self._lock:
            if pool_name not in self._pools:
                raise UnknownPoolError(pool_name)
            resources = self._resources[pool_name]
            idle = next((r for r in resources.values() if not r.in_use), None)
            if idle is None:
                if len(resources) >= self._pools[pool_name].max_size:
                    raise PoolExhaustedError(pool_name)
                idle = self._create_resource(pool_name)
            idle.in_use = True
            idle.use_count += 1
            idle.last_used_at = datetime.now(timezone.utc)
            return idle

    def release(self, pool_name: str, resource_id: str) -> PooledResource:
        with self._lock:
            if pool_name not in self._pools:
                raise UnknownPoolError(pool_name)
            resource = self._resources[pool_name].get(resource_id)
            if resource is None:
                raise UnknownResourceError(resource_id)
            if not resource.in_use:
                raise ResourceNotAcquiredError(resource_id)
            resource.in_use = False
            resource.last_used_at = datetime.now(timezone.utc)
            return resource

    def resize(
        self,
        pool_name: str,
        *,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
    ) -> ResourcePool:
        with self._lock:
            if pool_name not in self._pools:
                raise UnknownPoolError(pool_name)
            pool = self._pools[pool_name]
            new_min = pool.min_size if min_size is None else min_size
            new_max = pool.max_size if max_size is None else max_size
            if new_max <= 0:
                raise ValueError("max_size must be positive")
            if new_min < 0:
                raise ValueError("min_size must not be negative")
            if new_min > new_max:
                raise ValueError("min_size must not exceed max_size")

            pool.min_size = new_min
            pool.max_size = new_max

            resources = self._resources[pool_name]
            if len(resources) > new_max:
                idle_ids = [rid for rid, r in resources.items() if not r.in_use]
                excess = len(resources) - new_max
                for rid in idle_ids[:excess]:
                    del resources[rid]

            while len(resources) < new_min and len(resources) < new_max:
                self._create_resource(pool_name)

            return pool

    def cleanup_idle(self, pool_name: str, *, idle_timeout_seconds: Optional[float] = None) -> int:
        with self._lock:
            if pool_name not in self._pools:
                raise UnknownPoolError(pool_name)
            pool = self._pools[pool_name]
            timeout = idle_timeout_seconds if idle_timeout_seconds is not None else pool.idle_timeout_seconds
            if timeout is None:
                return 0
            resources = self._resources[pool_name]
            now = datetime.now(timezone.utc)
            expired = [
                rid
                for rid, r in resources.items()
                if not r.in_use and (now - r.last_used_at).total_seconds() >= timeout
            ]
            max_removable = max(0, len(resources) - pool.min_size)
            removable = expired[:max_removable]
            for rid in removable:
                del resources[rid]
            return len(removable)

    def stats(self, pool_name: str) -> dict:
        with self._lock:
            if pool_name not in self._pools:
                raise UnknownPoolError(pool_name)
            pool = self._pools[pool_name]
            resources = self._resources[pool_name]
            total = len(resources)
            in_use = sum(1 for r in resources.values() if r.in_use)
            available = total - in_use
            utilization_percent = (in_use / pool.max_size * 100) if pool.max_size else 0.0
            return {
                "name": pool_name,
                "pool_type": pool.pool_type.value,
                "min_size": pool.min_size,
                "max_size": pool.max_size,
                "size": total,
                "available": available,
                "in_use": in_use,
                "utilization_percent": utilization_percent,
            }

    def list_pools(self) -> list:
        with self._lock:
            return list(self._pools.values())


_resource_pool_manager = ResourcePoolManager()


def get_resource_pool_manager() -> ResourcePoolManager:
    return _resource_pool_manager
