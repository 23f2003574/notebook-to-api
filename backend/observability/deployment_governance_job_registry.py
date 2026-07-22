from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus


@dataclass(frozen=True)
class GovernanceJob:
    """
    A job's immutable identity and metadata, independent of any
    scheduling or execution state: what it is called, which namespace
    it belongs to, and whether it is currently enabled.
    """

    job_id: str

    name: str

    namespace: str

    description: str

    enabled: bool

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id must not be empty")

        if not self.name:
            raise ValueError("name must not be empty")

        if not self.namespace:
            raise ValueError("namespace must not be empty")

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "namespace": self.namespace,
            "description": self.description,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class JobRegistrationResult:
    """
    The outcome of one register() call: whether it was accepted, and
    if not, why.
    """

    accepted: bool

    reason: "str | None"

    def __post_init__(self) -> None:
        if self.accepted and self.reason is not None:
            raise ValueError(
                "reason must not be set when accepted is True"
            )

        if not self.accepted and self.reason is None:
            raise ValueError(
                "reason must be set when accepted is False"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
        }


class GovernanceJobRegistry:
    """
    Stores job metadata, validates registrations, and organizes jobs
    into namespaces, independent of any scheduler's own execution and
    timing concerns.

    register() is rejection-tolerant rather than exception-raising: a
    duplicate job_id or a duplicate name within a namespace is a
    routine outcome a caller should be able to branch on via the
    returned JobRegistrationResult, not an error condition. Every
    other mutating method (unregister/enable/disable/rename) raises
    KeyError for an unknown job_id, matching every other governance
    registry in this codebase.

    Job metadata is immutable after registration except for enabled
    (via enable()/disable()) and name (via rename()) — every other
    field is fixed at registration time.

    Thread-safe: every mutation is guarded by an internal lock, since
    jobs may be registered, queried, or modified from multiple threads
    concurrently.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._jobs: "dict[str, GovernanceJob]" = {}

        self._names: "set[tuple[str, str]]" = set()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

    def register(
        self,
        job_id: str,
        name: str,
        *,
        namespace: str = "default",
        description: str = "",
        enabled: bool = True,
    ) -> JobRegistrationResult:
        """
        Register a new job under the given job_id, or report why it
        was rejected: job_id already in use, or name already in use
        within namespace.
        """

        with self._lock:
            if job_id in self._jobs:
                return JobRegistrationResult(
                    accepted=False,
                    reason=f"job_id '{job_id}' is already registered",
                )

            key = (namespace, name)

            if key in self._names:
                return JobRegistrationResult(
                    accepted=False,
                    reason=(
                        f"job name '{name}' is already registered in "
                        f"namespace '{namespace}'"
                    ),
                )

            job = GovernanceJob(
                job_id=job_id,
                name=name,
                namespace=namespace,
                description=description,
                enabled=enabled,
                created_at=self._clock(),
            )

            self._jobs[job_id] = job
            self._names.add(key)

        self._publish(
            "job_registry_registered",
            job_id,
            {"name": name, "namespace": namespace},
        )

        return JobRegistrationResult(accepted=True, reason=None)

    def unregister(self, job_id: str) -> None:
        """
        Remove a registered job.

        Raises KeyError if job_id is not registered.
        """

        with self._lock:
            job = self._jobs.pop(job_id, None)

            if job is None:
                raise KeyError(
                    f"job '{job_id}' is not registered"
                )

            self._names.discard((job.namespace, job.name))

        self._publish(
            "job_registry_removed",
            job_id,
            {"name": job.name, "namespace": job.namespace},
        )

    def get(self, job_id: str) -> GovernanceJob:
        """
        Return the registered job for job_id.

        Raises KeyError if job_id is not registered.
        """

        with self._lock:
            job = self._jobs.get(job_id)

        if job is None:
            raise KeyError(f"job '{job_id}' is not registered")

        return job

    def exists(self, job_id: str) -> bool:
        """
        Return whether job_id is currently registered.
        """

        with self._lock:
            return job_id in self._jobs

    def list(self) -> "tuple[GovernanceJob, ...]":
        """
        Return every registered job, ordered by namespace, then name,
        then job_id, for deterministic output.
        """

        with self._lock:
            jobs = list(self._jobs.values())

        return tuple(
            sorted(
                jobs,
                key=lambda job: (job.namespace, job.name, job.job_id),
            )
        )

    def list_namespace(
        self, namespace: str
    ) -> "tuple[GovernanceJob, ...]":
        """
        Return every registered job in namespace, ordered by name then
        job_id.

        Returns an empty tuple if namespace has no registered jobs
        (namespaces are not registered as first-class entities of
        their own — they exist only implicitly, via the jobs
        registered under them).
        """

        with self._lock:
            jobs = [
                job
                for job in self._jobs.values()
                if job.namespace == namespace
            ]

        return tuple(
            sorted(jobs, key=lambda job: (job.name, job.job_id))
        )

    def enable(self, job_id: str) -> GovernanceJob:
        """
        Mark a registered job enabled and publish "job_enabled".

        Raises KeyError if job_id is not registered.
        """

        return self._set_enabled(job_id, True)

    def disable(self, job_id: str) -> GovernanceJob:
        """
        Mark a registered job disabled and publish "job_disabled".

        Raises KeyError if job_id is not registered.
        """

        return self._set_enabled(job_id, False)

    def rename(self, job_id: str, new_name: str) -> GovernanceJob:
        """
        Rename a registered job within its own namespace.

        Raises KeyError if job_id is not registered. Raises ValueError
        if new_name is already registered by a different job in the
        same namespace.
        """

        with self._lock:
            job = self._jobs.get(job_id)

            if job is None:
                raise KeyError(
                    f"job '{job_id}' is not registered"
                )

            new_key = (job.namespace, new_name)

            if new_name != job.name and new_key in self._names:
                raise ValueError(
                    f"job name '{new_name}' is already registered in "
                    f"namespace '{job.namespace}'"
                )

            self._names.discard((job.namespace, job.name))
            self._names.add(new_key)

            renamed = replace(job, name=new_name)
            self._jobs[job_id] = renamed

            return renamed

    def clear(self) -> None:
        """
        Remove every registered job.
        """

        with self._lock:
            self._jobs.clear()
            self._names.clear()

    def _set_enabled(self, job_id: str, enabled: bool) -> GovernanceJob:
        with self._lock:
            job = self._jobs.get(job_id)

            if job is None:
                raise KeyError(
                    f"job '{job_id}' is not registered"
                )

            updated = replace(job, enabled=enabled)
            self._jobs[job_id] = updated

        self._publish(
            "job_enabled" if enabled else "job_disabled",
            job_id,
            {"name": updated.name, "namespace": updated.namespace},
        )

        return updated

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


def build_default_governance_job_registry() -> GovernanceJobRegistry:
    """
    Build the process-wide governance job registry, wired to the
    process-wide governance event bus so job_registry_registered/
    job_registry_removed/job_enabled/job_disabled events are actually
    published, not just a documented possibility.
    """

    from .deployment_governance_event_bus import get_event_bus

    return GovernanceJobRegistry(event_bus=get_event_bus())


# Shared for the lifetime of the process: the scheduler delegates all
# job storage here, so registrations made through the scheduler (or
# directly through this registry's own API) need to be visible to
# both, which a persistence runtime built fresh per request cannot
# provide on its own.
_job_registry = build_default_governance_job_registry()


def get_job_registry() -> GovernanceJobRegistry:
    """
    Return the process-wide governance job registry.
    """

    return _job_registry
