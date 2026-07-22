from __future__ import annotations

import abc
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_scheduler_metrics import (
        GovernanceSchedulerMetrics,
    )


@dataclass(frozen=True)
class SchedulerLock:
    """
    One held distributed lock: which job it protects, who holds it,
    and when that lease expires.
    """

    job_id: str

    owner_id: str

    acquired_at: datetime

    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id must not be empty")

        if not self.owner_id:
            raise ValueError("owner_id must not be empty")

        if self.acquired_at.tzinfo is None:
            raise ValueError(
                "acquired_at must be timezone-aware"
            )

        if self.expires_at.tzinfo is None:
            raise ValueError(
                "expires_at must be timezone-aware"
            )

        if self.expires_at <= self.acquired_at:
            raise ValueError(
                "expires_at must be after acquired_at"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "owner_id": self.owner_id,
            "acquired_at": self.acquired_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True)
class LockAcquisitionResult:
    """
    The immutable outcome of one acquire() or renew() call.
    """

    acquired: bool

    owner_id: "str | None"

    expires_at: "datetime | None"

    def __post_init__(self) -> None:
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError(
                "expires_at must be timezone-aware"
            )

        if self.acquired and self.owner_id is None:
            raise ValueError(
                "owner_id must be set when acquired is True"
            )

        if self.acquired and self.expires_at is None:
            raise ValueError(
                "expires_at must be set when acquired is True"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "acquired": self.acquired,
            "owner_id": self.owner_id,
            "expires_at": (
                self.expires_at.isoformat()
                if self.expires_at is not None
                else None
            ),
        }


class LockProvider(abc.ABC):
    """
    The storage abstraction GovernanceSchedulerLockManager is built
    on: four primitive operations any backend (in-memory, a file,
    eventually Redis or PostgreSQL) can implement, with every TTL
    rule, ownership check, and event published entirely in the
    manager instead — a new provider never requires touching the
    manager (or the Scheduler that drives it) at all.

    Implementations are not required to be independently thread-safe:
    GovernanceSchedulerLockManager only ever calls a provider while
    holding its own internal lock, which is what actually serializes
    access within this process. A real distributed backend (Redis,
    PostgreSQL) would additionally need its own atomic compare-and-set
    semantics to be safe *across* processes/nodes, which is exactly
    the kind of backend-specific concern this interface exists to keep
    out of the manager.
    """

    @abc.abstractmethod
    def read(self, job_id: str) -> "SchedulerLock | None":
        """Return the currently stored lock for job_id, if any."""

    @abc.abstractmethod
    def write(self, lock: SchedulerLock) -> None:
        """Store lock, replacing whatever was stored for its job_id."""

    @abc.abstractmethod
    def delete(self, job_id: str) -> None:
        """Remove any stored lock for job_id. A no-op if there is none."""

    @abc.abstractmethod
    def list(self) -> "tuple[SchedulerLock, ...]":
        """Return every stored lock, ordered by job_id."""

    @abc.abstractmethod
    def config(self) -> "dict[str, object]":
        """
        Return a small, JSON-serializable description of this
        provider's configuration (not its current lock state) — what
        GovernanceJobPersistence persists.
        """


class InMemoryLockProvider(LockProvider):
    """
    The default lock provider: locks live only in this process's
    memory, for a single-node deployment or for tests.
    """

    def __init__(self) -> None:
        self._locks: "dict[str, SchedulerLock]" = {}

    def read(self, job_id: str) -> "SchedulerLock | None":
        return self._locks.get(job_id)

    def write(self, lock: SchedulerLock) -> None:
        self._locks[lock.job_id] = lock

    def delete(self, job_id: str) -> None:
        self._locks.pop(job_id, None)

    def list(self) -> "tuple[SchedulerLock, ...]":
        return tuple(
            sorted(self._locks.values(), key=lambda lock: lock.job_id)
        )

    def config(self) -> "dict[str, object]":
        return {"type": "memory"}


class FileLockProvider(LockProvider):
    """
    A lock provider backed by a single JSON file, shared by every
    process pointed at the same path — the closest this codebase gets
    to a genuinely cross-process lock without a real external store.

    Writes are atomic (temp file plus os.replace()), matching
    GovernanceJobPersistence's own file-backed writes. A missing or
    corrupted file is treated as "no locks stored" rather than raised,
    since a lock file is disposable state, not a durable record worth
    failing over.
    """

    def __init__(self, *, path: "str | Path") -> None:
        self._path = Path(path)

    def read(self, job_id: str) -> "SchedulerLock | None":
        entry = self._read_all().get(job_id)

        if entry is None:
            return None

        return self._from_dict(entry)

    def write(self, lock: SchedulerLock) -> None:
        document = self._read_all()
        document[lock.job_id] = lock.to_dict()
        self._write_all(document)

    def delete(self, job_id: str) -> None:
        document = self._read_all()

        if job_id in document:
            del document[job_id]
            self._write_all(document)

    def list(self) -> "tuple[SchedulerLock, ...]":
        locks = [
            self._from_dict(entry)
            for entry in self._read_all().values()
        ]

        return tuple(sorted(locks, key=lambda lock: lock.job_id))

    def config(self) -> "dict[str, object]":
        return {"type": "file", "path": str(self._path)}

    def _from_dict(self, entry: "dict[str, object]") -> SchedulerLock:
        return SchedulerLock(
            job_id=entry["job_id"],
            owner_id=entry["owner_id"],
            acquired_at=datetime.fromisoformat(entry["acquired_at"]),
            expires_at=datetime.fromisoformat(entry["expires_at"]),
        )

    def _read_all(self) -> "dict[str, object]":
        if not self._path.exists():
            return {}

        try:
            return json.loads(self._path.read_text())

        except (OSError, ValueError):
            return {}

    def _write_all(self, document: "dict[str, object]") -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = self._path.with_name(self._path.name + ".tmp")

        tmp_path.write_text(json.dumps(document, sort_keys=True))

        os.replace(tmp_path, self._path)


class GovernanceSchedulerLockManager:
    """
    Ensures a scheduled job is executed by only one scheduler instance
    at a time: acquire() grants a time-boxed lease rather than a
    permanent hold, so a node that crashes mid-job cannot strand the
    lock forever — it simply expires, and cleanup() (or the next
    acquire() attempt against it) reclaims it.

    Re-acquiring or renewing your own still-valid lease always
    succeeds (a lock is scoped to an owner_id, not to a single
    acquire() call) — only a *different* owner_id colliding with a
    still-valid lease is contention.

    Thread-safe: every operation is guarded by an internal lock, which
    is also what makes the pluggable LockProvider itself not need to
    be thread-safe on its own (see LockProvider's docstring).
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        provider: "LockProvider | None" = None,
        lease_seconds: int = 30,
        metrics: "GovernanceSchedulerMetrics | None" = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")

        self._lock = threading.Lock()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._provider = provider or InMemoryLockProvider()

        self._lease_seconds = lease_seconds

        self._metrics = metrics

    @property
    def provider(self) -> LockProvider:
        """
        The pluggable storage backend this manager is currently using.
        """

        return self._provider

    def acquire(
        self,
        job_id: str,
        owner_id: str,
        *,
        lease_seconds: "int | None" = None,
    ) -> LockAcquisitionResult:
        """
        Attempt to acquire job_id's lock for owner_id, granting a
        lease of lease_seconds (the manager's configured default if
        omitted).

        Succeeds if nothing currently holds the lock, the existing
        lease has already expired, or owner_id already holds it
        (reacquiring simply refreshes the lease). Otherwise reports
        contention: someone else's lease is still valid.
        """

        lease = (
            lease_seconds
            if lease_seconds is not None
            else self._lease_seconds
        )

        now = self._clock()

        with self._lock:
            existing = self._provider.read(job_id)

            can_acquire = (
                existing is None
                or existing.expires_at <= now
                or existing.owner_id == owner_id
            )

            if can_acquire:
                expires_at = now + timedelta(seconds=lease)

                self._provider.write(
                    SchedulerLock(
                        job_id=job_id,
                        owner_id=owner_id,
                        acquired_at=now,
                        expires_at=expires_at,
                    )
                )

        if can_acquire:
            self._publish(
                "lock_acquired",
                job_id,
                {
                    "owner_id": owner_id,
                    "expires_at": expires_at.isoformat(),
                },
            )

            return LockAcquisitionResult(
                acquired=True, owner_id=owner_id, expires_at=expires_at,
            )

        self._publish(
            "lock_contention",
            job_id,
            {"owner_id": owner_id, "held_by": existing.owner_id},
        )

        if self._metrics is not None:
            self._metrics.record_lock_contention()

        return LockAcquisitionResult(
            acquired=False,
            owner_id=existing.owner_id,
            expires_at=existing.expires_at,
        )

    def release(self, job_id: str, owner_id: str) -> bool:
        """
        Release job_id's lock, if owner_id currently holds it.

        Idempotent: releasing a lock that does not exist, has already
        expired, or is held by someone else is a no-op returning
        False rather than raising — a distributed lock's release path
        must never itself become a new failure mode.
        """

        with self._lock:
            existing = self._provider.read(job_id)

            released = existing is not None and existing.owner_id == owner_id

            if released:
                self._provider.delete(job_id)

        if released:
            self._publish(
                "lock_released", job_id, {"owner_id": owner_id}
            )

        return released

    def renew(
        self,
        job_id: str,
        owner_id: str,
        *,
        lease_seconds: "int | None" = None,
    ) -> LockAcquisitionResult:
        """
        Extend job_id's lease for owner_id (the manager's configured
        default lease_seconds if omitted) — the automatic-renewal
        primitive a long-running job's own callable can call partway
        through its own execution to keep holding the lock past its
        original expiry.

        Only succeeds if owner_id currently holds an unexpired lease;
        otherwise reports acquired=False without changing anything.
        """

        lease = (
            lease_seconds
            if lease_seconds is not None
            else self._lease_seconds
        )

        now = self._clock()

        with self._lock:
            existing = self._provider.read(job_id)

            can_renew = (
                existing is not None
                and existing.owner_id == owner_id
                and existing.expires_at > now
            )

            if can_renew:
                expires_at = now + timedelta(seconds=lease)

                self._provider.write(
                    SchedulerLock(
                        job_id=job_id,
                        owner_id=owner_id,
                        acquired_at=existing.acquired_at,
                        expires_at=expires_at,
                    )
                )

        if can_renew:
            self._publish(
                "lock_renewed",
                job_id,
                {
                    "owner_id": owner_id,
                    "expires_at": expires_at.isoformat(),
                },
            )

            return LockAcquisitionResult(
                acquired=True, owner_id=owner_id, expires_at=expires_at,
            )

        return LockAcquisitionResult(
            acquired=False,
            owner_id=existing.owner_id if existing else None,
            expires_at=existing.expires_at if existing else None,
        )

    def is_locked(self, job_id: str) -> bool:
        """
        Return whether job_id currently has an unexpired lock held by
        anyone.
        """

        with self._lock:
            existing = self._provider.read(job_id)

        return existing is not None and existing.expires_at > self._clock()

    def owner(self, job_id: str) -> "str | None":
        """
        Return job_id's current owner_id, or None if it is not
        currently locked (never locked, or its lease has expired).
        """

        with self._lock:
            existing = self._provider.read(job_id)

        now = self._clock()

        if existing is not None and existing.expires_at > now:
            return existing.owner_id

        return None

    def expired(self) -> "tuple[SchedulerLock, ...]":
        """
        Return every currently stored lock whose lease has already
        expired but has not yet been cleaned up, ordered by job_id.
        """

        now = self._clock()

        with self._lock:
            locks = self._provider.list()

        return tuple(
            sorted(
                (lock for lock in locks if lock.expires_at <= now),
                key=lambda lock: lock.job_id,
            )
        )

    def cleanup(self) -> int:
        """
        Remove every expired lock from the provider, publishing
        "lock_expired" for each, and return how many were removed.

        Deterministic: locks are always processed (and thus published)
        in job_id order.
        """

        now = self._clock()

        with self._lock:
            stale = sorted(
                (
                    lock
                    for lock in self._provider.list()
                    if lock.expires_at <= now
                ),
                key=lambda lock: lock.job_id,
            )

            for lock in stale:
                self._provider.delete(lock.job_id)

        for lock in stale:
            self._publish(
                "lock_expired", lock.job_id, {"owner_id": lock.owner_id}
            )

        return len(stale)

    def list(self) -> "tuple[SchedulerLock, ...]":
        """
        Return every currently stored lock (expired or not), ordered
        by job_id.
        """

        with self._lock:
            return self._provider.list()

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, object] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_lock_provider(config: "dict[str, object]") -> LockProvider:
    """
    Build a LockProvider from a config dict shaped like one of
    LockProvider.config()'s own return values — the same shape
    GovernanceJobPersistence persists (informationally; it never
    reconstructs a provider from it automatically, see
    GovernanceJobPersistence's own docstring for why).

    Raises ValueError for an unrecognized "type".
    """

    provider_type = config.get("type", "memory")

    if provider_type == "memory":
        return InMemoryLockProvider()

    if provider_type == "file":
        return FileLockProvider(path=config["path"])

    raise ValueError(f"unknown lock provider type '{provider_type}'")


def build_default_governance_scheduler_lock_manager() -> (
    GovernanceSchedulerLockManager
):
    """
    Build the process-wide governance scheduler lock manager, wired to
    the process-wide governance event bus, defaulting to an
    in-memory provider (a single process has no distributed peers to
    coordinate with in the first place).
    """

    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_scheduler_metrics import (
        get_scheduler_metrics,
    )

    return GovernanceSchedulerLockManager(
        event_bus=get_event_bus(), metrics=get_scheduler_metrics(),
    )


# Shared for the lifetime of the process: locks acquired through the
# scheduler's own tick need to be visible to whatever queries the
# manager directly, which a persistence runtime built fresh per
# request cannot provide on its own.
_scheduler_lock_manager = build_default_governance_scheduler_lock_manager()


def get_scheduler_lock_manager() -> GovernanceSchedulerLockManager:
    """
    Return the process-wide governance scheduler lock manager.
    """

    return _scheduler_lock_manager
