from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_cron import GovernanceCronScheduler
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_execution_manager import (
        GovernanceExecutionManager,
    )
    from .deployment_governance_job_dependencies import (
        GovernanceJobDependencyManager,
    )
    from .deployment_governance_job_registry import GovernanceJobRegistry
    from .deployment_governance_retry import GovernanceRetryEngine
    from .deployment_governance_scheduler import GovernanceScheduler
    from .deployment_governance_scheduler_locks import (
        GovernanceSchedulerLockManager,
    )
    from .deployment_governance_trigger_engine import GovernanceTriggerEngine

# The current on-disk/in-memory document shape this module writes and
# understands. load() rejects (gracefully, not by raising) any stored
# document whose version is newer than this — an older version is
# always readable, since there is only one version so far and any
# future version bump is expected to keep reading everything this one
# wrote.
CURRENT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PersistenceSnapshot:
    """
    A summary of the currently stored snapshot (not of this process's
    live in-memory state): its schema version, when it was saved, and
    how many of each kind of record it contains.
    """

    version: int

    created_at: datetime

    jobs: int

    triggers: int

    pending_retries: int

    def __post_init__(self) -> None:
        if self.version < 0:
            raise ValueError("version must be >= 0")

        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware"
            )

        if self.jobs < 0:
            raise ValueError("jobs must be >= 0")

        if self.triggers < 0:
            raise ValueError("triggers must be >= 0")

        if self.pending_retries < 0:
            raise ValueError("pending_retries must be >= 0")

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "jobs": self.jobs,
            "triggers": self.triggers,
            "pending_retries": self.pending_retries,
        }


@dataclass(frozen=True)
class RestoreCounts:
    """
    How many of each kind of record the most recent load()/_do_load()
    call actually restored, as opposed to PersistenceSnapshot (which
    describes the stored document, not what a restore did with it).

    Exists specifically so a caller like GovernanceSchedulerBootstrap
    can report restored_jobs/restored_triggers/restored_retry_queue
    counts in its own bootstrap report without parsing load()'s
    human-readable PersistenceResult.message string.
    """

    jobs: int

    triggers: int

    pending_retries: int

    def __post_init__(self) -> None:
        for field_name in ("jobs", "triggers", "pending_retries"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")

    def to_dict(self) -> dict[str, object]:
        return {
            "jobs": self.jobs,
            "triggers": self.triggers,
            "pending_retries": self.pending_retries,
        }


@dataclass(frozen=True)
class PersistenceResult:
    """
    The immutable outcome of one persistence operation.
    """

    success: bool

    operation: str

    duration_ms: int

    message: "str | None"

    def __post_init__(self) -> None:
        if not self.operation:
            raise ValueError("operation must not be empty")

        if self.duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "operation": self.operation,
            "duration_ms": self.duration_ms,
            "message": self.message,
        }


class GovernanceJobPersistence:
    """
    Durable, storage-agnostic persistence for scheduler state: jobs,
    trigger definitions, and the pending retry queue, so a process
    restart does not lose them.

    Storage-agnostic in the same sense DeploymentGovernancePersistence
    is: constructed with a path, it persists one versioned JSON
    document to that file (written atomically, via a temp file plus
    os.replace()); constructed without one, it holds that same
    document in memory instead (the default, matching how MEMORY is
    this codebase's other persistence layer's default backend too —
    safe, side-effect-free unless a path is explicitly configured).

    This is a coordination layer, not a source of truth of its own:
    everything it persists is read from (and restored into) the
    GovernanceJobRegistry / GovernanceTriggerEngine / GovernanceRetryEngine
    it was constructed with via their own already-public APIs, plus
    one small accessor (GovernanceRetryEngine.pending_context()) added
    specifically to let a pending retry's job_id/policy_id round-trip
    through a snapshot, since RetryAttempt itself intentionally omits
    them.

    Restoring never fails loudly: an entry already present (by
    job_id/trigger job_id/execution_id) is left alone rather than
    rejected, and a malformed individual entry is skipped rather than
    aborting the whole restore. Restored jobs and triggers are minted
    fresh created_at/trigger_id values at restore time (their
    identity — job_id, trigger's job_id, and job name/namespace — is
    what is preserved, not every original timestamp or generated ID).

    A wired GovernanceSchedulerLockManager is the one exception to
    "everything here is restorable": save() records its provider's
    config() (e.g. "file, at this path") purely for observability, and
    load() never acts on it — active lock state is explicitly out of
    scope for this layer (a lease is meant to expire and be reclaimed,
    not be resurrected from a stale snapshot), and there is no manager
    method for swapping a live provider out safely regardless.

    Thread-safe: every read/write of the stored document is guarded by
    an internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
        job_registry: "GovernanceJobRegistry | None" = None,
        trigger_engine: "GovernanceTriggerEngine | None" = None,
        retry_engine: "GovernanceRetryEngine | None" = None,
        scheduler: "GovernanceScheduler | None" = None,
        execution_manager: "GovernanceExecutionManager | None" = None,
        cron_scheduler: "GovernanceCronScheduler | None" = None,
        dependency_manager: (
            "GovernanceJobDependencyManager | None"
        ) = None,
        lock_manager: (
            "GovernanceSchedulerLockManager | None"
        ) = None,
        path: "str | Path | None" = None,
        include_execution_history: bool = False,
    ) -> None:
        self._lock = threading.Lock()

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._job_registry = job_registry

        self._trigger_engine = trigger_engine

        self._retry_engine = retry_engine

        self._scheduler = scheduler

        self._execution_manager = execution_manager

        self._cron_scheduler = cron_scheduler

        self._dependency_manager = dependency_manager

        self._lock_manager = lock_manager

        self._path = Path(path) if path is not None else None

        self._memory_document: "dict[str, object] | None" = None

        self._include_execution_history = include_execution_history

        self._last_restore: "RestoreCounts | None" = None

    def save(self) -> PersistenceResult:
        """
        Serialize every registered job, trigger, and pending retry
        (plus whether the scheduler was running) into one versioned
        document, and write it atomically.
        """

        def _do() -> "tuple[bool, str | None]":
            document = self._build_document()

            with self._lock:
                self._write(document)

            return True, None

        return self._run("save", _do)

    def load(self) -> PersistenceResult:
        """
        Read the stored document (if any) and restore its jobs,
        triggers, and pending retries into the components this was
        constructed with, then start the scheduler if it was recorded
        as running.

        A missing document is not an error: it returns success=True
        with an explanatory message and restores nothing. A corrupted
        or schema-incompatible document is handled gracefully too:
        success=False with an explanatory message, never a raised
        exception, and nothing is partially restored.
        """

        return self._run("load", self._do_load)

    def save_jobs(self) -> PersistenceResult:
        """
        Persist only the registered-jobs section of the stored
        document, leaving triggers/pending retries/scheduler state
        exactly as they were last saved (an empty document if nothing
        has been saved yet).
        """

        def _do() -> "tuple[bool, str | None]":
            with self._lock:
                document = self._read_locked() or self._empty_document()
                document["jobs"] = self._serialize_jobs()
                document["version"] = CURRENT_SCHEMA_VERSION
                document["created_at"] = self._clock().isoformat()
                self._write(document)

            return True, None

        return self._run("save_jobs", _do)

    def load_jobs(self) -> PersistenceResult:
        """
        Restore only the jobs section of the stored document.
        """

        def _do() -> "tuple[bool, str | None]":
            with self._lock:
                document = self._read_locked()

            if document is None:
                return True, "no snapshot found"

            restored = self._restore_jobs(document.get("jobs", []))

            return True, f"restored {restored} job(s)"

        return self._run("load_jobs", _do)

    def save_triggers(self) -> PersistenceResult:
        """
        Persist only the trigger-definitions section of the stored
        document.
        """

        def _do() -> "tuple[bool, str | None]":
            with self._lock:
                document = self._read_locked() or self._empty_document()
                document["triggers"] = self._serialize_triggers()
                document["version"] = CURRENT_SCHEMA_VERSION
                document["created_at"] = self._clock().isoformat()
                self._write(document)

            return True, None

        return self._run("save_triggers", _do)

    def load_triggers(self) -> PersistenceResult:
        """
        Restore only the triggers section of the stored document.
        """

        def _do() -> "tuple[bool, str | None]":
            with self._lock:
                document = self._read_locked()

            if document is None:
                return True, "no snapshot found"

            restored = self._restore_triggers(document.get("triggers", []))

            return True, f"restored {restored} trigger(s)"

        return self._run("load_triggers", _do)

    def save_retry_queue(self) -> PersistenceResult:
        """
        Persist only the pending-retry-queue section of the stored
        document.
        """

        def _do() -> "tuple[bool, str | None]":
            with self._lock:
                document = self._read_locked() or self._empty_document()
                document["pending_retries"] = (
                    self._serialize_pending_retries()
                )
                document["version"] = CURRENT_SCHEMA_VERSION
                document["created_at"] = self._clock().isoformat()
                self._write(document)

            return True, None

        return self._run("save_retry_queue", _do)

    def load_retry_queue(self) -> PersistenceResult:
        """
        Restore only the pending-retry-queue section of the stored
        document.
        """

        def _do() -> "tuple[bool, str | None]":
            with self._lock:
                document = self._read_locked()

            if document is None:
                return True, "no snapshot found"

            restored = self._restore_pending_retries(
                document.get("pending_retries", [])
            )

            return True, f"restored {restored} pending retry(ies)"

        return self._run("load_retry_queue", _do)

    def snapshot(self) -> PersistenceSnapshot:
        """
        Return a summary of the currently stored snapshot (not of this
        process's live state) — a zeroed, version-0 placeholder if
        nothing has ever been saved, or if the stored document is
        corrupted beyond even reading its counts.
        """

        with self._lock:
            try:
                document = self._read_locked()

            except Exception:
                document = None

        if not document:
            return PersistenceSnapshot(
                version=0,
                created_at=self._clock(),
                jobs=0,
                triggers=0,
                pending_retries=0,
            )

        try:
            created_at = datetime.fromisoformat(document["created_at"])

        except (KeyError, TypeError, ValueError):
            created_at = self._clock()

        return PersistenceSnapshot(
            version=int(document.get("version", 0)),
            created_at=created_at,
            jobs=len(document.get("jobs", []) or []),
            triggers=len(document.get("triggers", []) or []),
            pending_retries=len(document.get("pending_retries", []) or []),
        )

    def last_restore(self) -> "RestoreCounts | None":
        """
        Return how many jobs/triggers/pending retries the most recent
        load() call restored, or None if load() has never run.

        Unlike snapshot() (which re-reads the stored document fresh
        on every call), this returns a value cached from the last
        actual restore — a fresh reader would have nothing to report
        without repeating the restore itself.
        """

        return self._last_restore

    def clear(self) -> None:
        """
        Delete the stored snapshot, if any. A subsequent load() then
        sees "no snapshot found".
        """

        with self._lock:
            if self._path is not None:
                self._path.unlink(missing_ok=True)

            else:
                self._memory_document = None

    def _do_load(self) -> "tuple[bool, str | None]":
        with self._lock:
            try:
                document = self._read_locked()

            except Exception as exc:
                return False, f"corrupted snapshot: {exc}"

        if document is None:
            self._last_restore = RestoreCounts(
                jobs=0, triggers=0, pending_retries=0
            )

            return True, "no snapshot found"

        try:
            version = document["version"]
            jobs = document["jobs"]
            triggers = document["triggers"]
            pending_retries = document["pending_retries"]

        except (KeyError, TypeError) as exc:
            self._last_restore = RestoreCounts(
                jobs=0, triggers=0, pending_retries=0
            )

            return False, f"corrupted snapshot: missing field {exc}"

        if not isinstance(version, int) or version > CURRENT_SCHEMA_VERSION:
            self._last_restore = RestoreCounts(
                jobs=0, triggers=0, pending_retries=0
            )

            return (
                False,
                f"unsupported snapshot schema version {version!r}",
            )

        restored_jobs = self._restore_jobs(jobs)
        restored_triggers = self._restore_triggers(triggers)
        restored_retries = self._restore_pending_retries(pending_retries)

        self._last_restore = RestoreCounts(
            jobs=restored_jobs,
            triggers=restored_triggers,
            pending_retries=restored_retries,
        )

        # cron_triggers and dependencies were both added after this
        # document format's first version; a v1 document saved before
        # either existed simply has no such key, and that must load
        # exactly as successfully as one that does — hence .get(), not
        # required-field indexing.
        restored_cron = self._restore_cron_triggers(
            document.get("cron_triggers", [])
        )
        restored_dependencies = self._restore_dependencies(
            document.get("dependencies", [])
        )

        if self._scheduler is not None and document.get(
            "scheduler_running"
        ):
            self._scheduler.start()

        # lock_provider_config is deliberately never acted on here: it
        # is persisted for observability only ("what provider was this
        # configured with when last saved"), not active lock state —
        # reconstructing and swapping a live GovernanceSchedulerLock-
        # Manager's provider out from under it has no corresponding
        # manager method, and silently rebuilding one here would risk
        # discarding in-flight locks a real multi-node deployment is
        # relying on.

        return True, (
            f"restored {restored_jobs} job(s), {restored_triggers} "
            f"trigger(s), {restored_retries} pending retry(ies), "
            f"{restored_cron} cron trigger(s), {restored_dependencies} "
            "dependency definition(s)"
        )

    def _build_document(self) -> "dict[str, object]":
        return {
            "version": CURRENT_SCHEMA_VERSION,
            "created_at": self._clock().isoformat(),
            "jobs": self._serialize_jobs(),
            "triggers": self._serialize_triggers(),
            "pending_retries": self._serialize_pending_retries(),
            "cron_triggers": self._serialize_cron_triggers(),
            "dependencies": self._serialize_dependencies(),
            "scheduler_running": (
                self._scheduler.status().running
                if self._scheduler is not None
                else False
            ),
            "lock_provider_config": (
                self._lock_manager.provider.config()
                if self._lock_manager is not None
                else None
            ),
            **(
                {
                    "execution_history": [
                        result.to_dict()
                        for result in self._execution_manager.history()
                    ]
                }
                if self._include_execution_history
                and self._execution_manager is not None
                else {}
            ),
        }

    def _empty_document(self) -> "dict[str, object]":
        return {
            "version": CURRENT_SCHEMA_VERSION,
            "created_at": self._clock().isoformat(),
            "jobs": [],
            "triggers": [],
            "pending_retries": [],
            "cron_triggers": [],
            "dependencies": [],
            "scheduler_running": False,
            "lock_provider_config": None,
        }

    def _serialize_jobs(self) -> "list[dict[str, object]]":
        if self._job_registry is None:
            return []

        return sorted(
            (job.to_dict() for job in self._job_registry.list()),
            key=lambda entry: entry["job_id"],
        )

    def _serialize_triggers(self) -> "list[dict[str, object]]":
        if self._trigger_engine is None:
            return []

        return sorted(
            (
                trigger.to_dict()
                for trigger in self._trigger_engine.list()
            ),
            key=lambda entry: entry["trigger_id"],
        )

    def _serialize_pending_retries(self) -> "list[dict[str, object]]":
        if self._retry_engine is None:
            return []

        entries = []

        for attempt in self._retry_engine.pending():
            entry = attempt.to_dict()
            context = self._retry_engine.pending_context(
                attempt.execution_id
            )

            if context is not None:
                entry["job_id"], entry["policy_id"] = context

            entries.append(entry)

        return sorted(entries, key=lambda entry: entry["execution_id"])

    def _serialize_cron_triggers(self) -> "list[dict[str, object]]":
        if self._cron_scheduler is None:
            return []

        return sorted(
            (
                trigger.to_dict()
                for trigger in self._cron_scheduler.list()
            ),
            key=lambda entry: entry["trigger_id"],
        )

    def _restore_cron_triggers(self, entries: "list[object]") -> int:
        if self._cron_scheduler is None:
            return 0

        restored = 0

        for entry in entries:
            try:
                self._cron_scheduler.register(
                    entry["job_id"],
                    expression=entry["expression"],
                    timezone=entry.get("timezone", "UTC"),
                    enabled=entry.get("enabled", True),
                )

                restored += 1

            except (KeyError, TypeError, ValueError):
                continue

        return restored

    def _serialize_dependencies(self) -> "list[dict[str, object]]":
        if self._dependency_manager is None:
            return []

        entries = []

        for job_id in self._dependency_manager.validate().startup_order:
            try:
                depends_on = self._dependency_manager.dependencies(job_id)

            except KeyError:
                # A leaf job pulled in by validate()'s startup_order
                # purely because something else depends on it, with no
                # JobDependency entry of its own — nothing to persist.
                continue

            entries.append(
                {"job_id": job_id, "depends_on": list(depends_on)}
            )

        return sorted(entries, key=lambda entry: entry["job_id"])

    def _restore_dependencies(self, entries: "list[object]") -> int:
        if self._dependency_manager is None:
            return 0

        restored = 0

        for entry in entries:
            try:
                self._dependency_manager.register(
                    entry["job_id"],
                    depends_on=tuple(entry.get("depends_on", ())),
                )

                restored += 1

            except (KeyError, TypeError, ValueError):
                continue

        return restored

    def _restore_jobs(self, entries: "list[object]") -> int:
        if self._job_registry is None:
            return 0

        restored = 0

        for entry in entries:
            try:
                job_id = entry["job_id"]

                if self._job_registry.exists(job_id):
                    continue

                result = self._job_registry.register(
                    job_id,
                    entry["name"],
                    namespace=entry.get("namespace", "default"),
                    description=entry.get("description", ""),
                    enabled=entry.get("enabled", True),
                )

                if result.accepted:
                    restored += 1

            except (KeyError, TypeError):
                continue

        return restored

    def _restore_triggers(self, entries: "list[object]") -> int:
        if self._trigger_engine is None:
            return 0

        restored = 0

        for entry in entries:
            try:
                next_run_raw = entry.get("next_run")

                next_run = (
                    datetime.fromisoformat(next_run_raw)
                    if next_run_raw
                    else None
                )

                self._trigger_engine.register(
                    entry["job_id"],
                    trigger_type=entry["trigger_type"],
                    next_run=next_run,
                    enabled=entry.get("enabled", True),
                )

                restored += 1

            except (KeyError, TypeError, ValueError):
                continue

        return restored

    def _restore_pending_retries(self, entries: "list[object]") -> int:
        if self._retry_engine is None:
            return 0

        restored = 0

        for entry in entries:
            try:
                execution_id = entry["execution_id"]
                job_id = entry["job_id"]
                policy_id = entry["policy_id"]

                self._retry_engine.schedule_retry(
                    execution_id,
                    policy_id,
                    job_id=job_id,
                    reason=entry.get("reason"),
                )

                restored += 1

            except (KeyError, TypeError, ValueError):
                continue

        return restored

    def _write(self, document: "dict[str, object]") -> None:
        """
        Store document as the current snapshot. Must be called while
        holding self._lock.

        File-backed writes are atomic: the full document is written to
        a temp file in the same directory, then moved into place with
        os.replace(), which is an atomic rename on POSIX — a reader
        never observes a partially written file.

        Memory-backed "writes" are atomic by construction: the whole
        document is swapped in with one attribute assignment.
        """

        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

            tmp_path = self._path.with_name(
                self._path.name + ".tmp"
            )

            tmp_path.write_text(
                json.dumps(document, sort_keys=True)
            )

            os.replace(tmp_path, self._path)

        else:
            self._memory_document = document

    def _read_locked(self) -> "dict[str, object] | None":
        """
        Return the currently stored document, or None if nothing has
        been saved yet. Must be called while holding self._lock.

        Raises whatever json.loads() raises for a corrupted file — the
        caller (load()/_do_load()) is responsible for catching that
        and turning it into a graceful PersistenceResult.
        """

        if self._path is not None:
            if not self._path.exists():
                return None

            return json.loads(self._path.read_text())

        return self._memory_document

    def _run(
        self,
        operation: str,
        fn: "Callable[[], tuple[bool, str | None]]",
    ) -> PersistenceResult:
        started_at = self._clock()

        try:
            success, message = fn()

        except Exception as exc:
            success, message = False, str(exc)

        duration_ms = max(
            0,
            int(
                (self._clock() - started_at).total_seconds() * 1000
            ),
        )

        result = PersistenceResult(
            success=success,
            operation=operation,
            duration_ms=duration_ms,
            message=message,
        )

        if not success:
            self._publish(
                "persistence_failed",
                {"operation": operation, "message": message},
            )

        elif operation.startswith("save"):
            self._publish(
                "persistence_saved",
                {"operation": operation, "message": message},
            )
            self._publish("snapshot_created", {"operation": operation})

        elif operation.startswith("load"):
            self._publish(
                "persistence_loaded",
                {"operation": operation, "message": message},
            )

        return result

    def _publish(
        self, event_type: str, payload: "dict[str, object]"
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source="persistence", payload=payload
        )


def build_default_governance_job_persistence() -> GovernanceJobPersistence:
    """
    Build the process-wide governance job persistence layer, wired to
    every other process-wide governance singleton, defaulting to
    in-memory storage (no path configured) — the same safe default
    DeploymentGovernancePersistenceConfig uses for its own backend,
    so importing this module never performs file I/O on its own.
    """

    from .deployment_governance_cron import get_cron_scheduler
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_execution_manager import (
        get_execution_manager,
    )
    from .deployment_governance_job_dependencies import (
        get_job_dependency_manager,
    )
    from .deployment_governance_job_registry import get_job_registry
    from .deployment_governance_retry import get_retry_engine
    from .deployment_governance_scheduler import get_scheduler
    from .deployment_governance_scheduler_locks import (
        get_scheduler_lock_manager,
    )
    from .deployment_governance_trigger_engine import get_trigger_engine

    return GovernanceJobPersistence(
        event_bus=get_event_bus(),
        job_registry=get_job_registry(),
        trigger_engine=get_trigger_engine(),
        retry_engine=get_retry_engine(),
        scheduler=get_scheduler(),
        execution_manager=get_execution_manager(),
        cron_scheduler=get_cron_scheduler(),
        dependency_manager=get_job_dependency_manager(),
        lock_manager=get_scheduler_lock_manager(),
    )


# Shared for the lifetime of the process: a save()/load() triggered
# through the API needs to act on the same live singletons any other
# request or component sees, which a persistence runtime built fresh
# per request cannot provide on its own.
_job_persistence = build_default_governance_job_persistence()


def get_job_persistence() -> GovernanceJobPersistence:
    """
    Return the process-wide governance job persistence layer.
    """

    return _job_persistence
