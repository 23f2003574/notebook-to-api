from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus

# A pragmatic (not fully spec-compliant) semantic version pattern:
# MAJOR.MINOR.PATCH, with optional -prerelease and +build metadata
# suffixes, e.g. "1.2.3", "2.0.0-rc.1", "1.0.0+build.42".
_SEMVER_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

_CHECKSUM_LENGTH = 64


def is_semantic_version(value: str) -> bool:
    """
    Whether value is a valid MAJOR.MINOR.PATCH semantic version
    (with optional -prerelease/+build suffixes).

    Public (unlike _is_sha256_hex below): deployment_governance_
    blue_green validates blue_version/green_version against the same
    rule this registry uses for DeploymentVersion.version, so both
    modules share one definition instead of drifting apart.
    """

    return bool(_SEMVER_PATTERN.match(value))


def _is_sha256_hex(value: str) -> bool:
    """
    Matching deployment_governance_audit._is_sha256_hex: a checksum is
    a 64-character lowercase or uppercase hex string (a SHA-256
    digest), not necessarily one this registry computed itself — the
    caller supplies the artifact's checksum, and this only validates
    its shape.
    """

    if len(value) != _CHECKSUM_LENGTH:
        return False

    try:
        int(value, 16)

    except ValueError:
        return False

    return True


# The lifecycle a deployment's revision history records: REGISTERED
# for the first version ever recorded for a deployment_id, UPDATED
# for every subsequent version, and REMOVED as a tombstone entry
# appended (never in place of an earlier one) when the deployment is
# removed from the active registry.
REVISION_STATES: "tuple[str, ...]" = ("REGISTERED", "UPDATED", "REMOVED")


@dataclass(frozen=True)
class DeploymentVersion:
    """
    One deployment's current registered version and artifact.

    metadata is stored as a read-only mapping rather than a plain
    dict: a frozen dataclass only blocks reassigning its fields, not
    mutating a dict stored in one, so wrapping it is what actually
    makes this immutable, matching GovernanceEvent.payload in
    deployment_governance_event_bus.
    """

    deployment_id: str

    version: str

    artifact: str

    checksum: str

    created_at: datetime

    metadata: "dict[str, Any]"

    def __post_init__(self) -> None:
        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not is_semantic_version(self.version):
            raise ValueError(
                f"version '{self.version}' is not a valid semantic "
                "version (expected MAJOR.MINOR.PATCH)"
            )

        if not self.artifact:
            raise ValueError("artifact must not be empty")

        if not _is_sha256_hex(self.checksum):
            raise ValueError(
                f"checksum must be a {_CHECKSUM_LENGTH}-character "
                "hex string"
            )

        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

        object.__setattr__(
            self, "metadata", MappingProxyType(dict(self.metadata))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "version": self.version,
            "artifact": self.artifact,
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DeploymentRevision:
    """
    One immutable entry in a deployment's append-only revision
    history.
    """

    revision_id: str

    deployment_id: str

    version: str

    state: str

    created_at: datetime

    def __post_init__(self) -> None:
        if not self.revision_id:
            raise ValueError("revision_id must not be empty")

        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if self.state not in REVISION_STATES:
            raise ValueError(
                f"state must be one of {REVISION_STATES}"
            )

        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "revision_id": self.revision_id,
            "deployment_id": self.deployment_id,
            "version": self.version,
            "state": self.state,
            "created_at": self.created_at.isoformat(),
        }


class DeploymentVersionRegistry:
    """
    The centralized source of truth for which version/artifact each
    deployment is currently registered at, plus a complete append-only
    revision history for future rollback support.

    The Rollout Manager (deployment_governance_rollout_manager)
    resolves deployment_id against this registry rather than storing
    deployment metadata of its own.

    Thread-safe: every mutation is guarded by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        event_bus: "GovernanceEventBus | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._versions: "dict[str, DeploymentVersion]" = {}

        self._history: "dict[str, list[DeploymentRevision]]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

    def register(
        self,
        deployment_id: str,
        version: str,
        artifact: str,
        checksum: str,
        metadata: "dict[str, Any] | None" = None,
    ) -> DeploymentVersion:
        """
        Register deployment_id's first (or, after remove(), next)
        version.

        Raises ValueError if deployment_id currently has an active
        registration, if version is not a valid semantic version, or
        if checksum is not a well-formed SHA-256 hex digest.
        """

        if not deployment_id:
            raise ValueError("deployment_id must not be empty")

        with self._lock:
            if deployment_id in self._versions:
                raise ValueError(
                    f"deployment '{deployment_id}' is already "
                    "registered"
                )

            now = self._clock()

            record = DeploymentVersion(
                deployment_id=deployment_id,
                version=version,
                artifact=artifact,
                checksum=checksum,
                created_at=now,
                metadata=metadata or {},
            )

            self._versions[deployment_id] = record

            revision = DeploymentRevision(
                revision_id=str(uuid4()),
                deployment_id=deployment_id,
                version=version,
                state="REGISTERED",
                created_at=now,
            )

            self._history.setdefault(deployment_id, []).append(
                revision
            )

        self._publish(
            "deployment_registered",
            deployment_id,
            {"version": version, "artifact": artifact},
        )

        self._publish(
            "deployment_revision_created",
            deployment_id,
            revision.to_dict(),
        )

        return record

    def update(
        self,
        deployment_id: str,
        version: str,
        artifact: str,
        checksum: str,
        metadata: "dict[str, Any] | None" = None,
    ) -> DeploymentVersion:
        """
        Replace deployment_id's currently registered version, and
        append a new UPDATED revision to its history (the previous
        revision entries are left exactly as they were — history is
        append-only, never rewritten).

        Raises KeyError if deployment_id is not currently registered,
        ValueError if version is not a valid semantic version, or if
        checksum is not a well-formed SHA-256 hex digest.
        """

        with self._lock:
            if deployment_id not in self._versions:
                raise KeyError(
                    f"deployment '{deployment_id}' is not registered"
                )

            now = self._clock()

            record = DeploymentVersion(
                deployment_id=deployment_id,
                version=version,
                artifact=artifact,
                checksum=checksum,
                created_at=now,
                metadata=metadata or {},
            )

            self._versions[deployment_id] = record

            revision = DeploymentRevision(
                revision_id=str(uuid4()),
                deployment_id=deployment_id,
                version=version,
                state="UPDATED",
                created_at=now,
            )

            self._history[deployment_id].append(revision)

        self._publish(
            "deployment_updated",
            deployment_id,
            {"version": version, "artifact": artifact},
        )

        self._publish(
            "deployment_revision_created",
            deployment_id,
            revision.to_dict(),
        )

        return record

    def remove(self, deployment_id: str) -> None:
        """
        Remove deployment_id's active registration, appending a final
        REMOVED tombstone revision — the deployment's history remains
        queryable via history() even after removal, for rollback
        support.

        Raises KeyError if deployment_id is not currently registered.
        """

        with self._lock:
            record = self._versions.pop(deployment_id, None)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' is not registered"
                )

            now = self._clock()

            revision = DeploymentRevision(
                revision_id=str(uuid4()),
                deployment_id=deployment_id,
                version=record.version,
                state="REMOVED",
                created_at=now,
            )

            self._history[deployment_id].append(revision)

        self._publish("deployment_removed", deployment_id, {})

    def get(self, deployment_id: str) -> DeploymentVersion:
        """
        Return deployment_id's currently registered version.

        Raises KeyError if deployment_id is not currently registered
        (including if it was removed).
        """

        with self._lock:
            record = self._versions.get(deployment_id)

            if record is None:
                raise KeyError(
                    f"deployment '{deployment_id}' is not registered"
                )

            return record

    def latest(self, deployment_id: str) -> DeploymentVersion:
        """
        Return deployment_id's most recently registered version.

        Identical to get(): this registry only ever tracks one active
        version per deployment_id, so "current" and "latest" are the
        same record. Provided as its own method so callers can express
        "what should currently be live" without implying the more
        general "currently registered" framing get() carries.
        """

        return self.get(deployment_id)

    def history(
        self, deployment_id: str
    ) -> "tuple[DeploymentRevision, ...]":
        """
        Return every revision ever recorded for deployment_id, in
        order (oldest first), including entries recorded before a
        later removal. Returns an empty tuple if deployment_id has
        never been registered.
        """

        with self._lock:
            return tuple(self._history.get(deployment_id, ()))

    def exists(self, deployment_id: str) -> bool:
        """
        Return whether deployment_id currently has an active
        registration.
        """

        with self._lock:
            return deployment_id in self._versions

    def list(self) -> "tuple[DeploymentVersion, ...]":
        """
        Return every currently registered deployment version, ordered
        deterministically by deployment_id.
        """

        with self._lock:
            records = list(self._versions.values())

        return tuple(
            sorted(records, key=lambda record: record.deployment_id)
        )

    def clear(self) -> None:
        """
        Remove every registered deployment and its revision history.
        """

        with self._lock:
            self._versions.clear()
            self._history.clear()

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, Any] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_default_governance_version_registry() -> (
    DeploymentVersionRegistry
):
    """
    Build the process-wide governance deployment version registry,
    wired to the process-wide governance event bus.
    """

    from .deployment_governance_event_bus import get_event_bus

    return DeploymentVersionRegistry(event_bus=get_event_bus())


# Shared for the lifetime of the process: which deployments are
# currently registered, and their complete revision history, is
# inherently process-wide, not something that can be meaningfully
# rebuilt fresh per request.
_version_registry = build_default_governance_version_registry()


def get_version_registry() -> DeploymentVersionRegistry:
    """
    Return the process-wide governance deployment version registry.
    """

    return _version_registry
