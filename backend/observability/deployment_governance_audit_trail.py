from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_audit import (
        AuditRecord,
        GovernanceAuditService,
    )

# The categories of action this service expects to find recorded —
# documented vocabulary, not a closed set enforced anywhere (matching
# GOVERNANCE_EVENT_TYPES' own "documented, not enforced" contract).
# Authorization and Policy actions are already recorded by
# DeploymentRBACEngine.authorize() and DeploymentRolloutPolicyEngine
# respectively; Approval and Configuration by DeploymentApprovalEngine
# and DeploymentRBACEngine's own role-management methods, as of this
# commit. Authentication, Deployment, and Rollback recording is left
# to whichever later commit wires each of those in — this service
# itself only ever queries whatever GovernanceAuditService already
# holds, it does not require any particular category to be present.
RECORDED_AUDIT_ACTION_CATEGORIES: "tuple[str, ...]" = (
    "Authentication",
    "Authorization",
    "Approval",
    "Deployment",
    "Rollback",
    "Policy",
    "Configuration",
)


@dataclass(frozen=True)
class AuditEvent:
    """
    An immutable, simplified view of one GovernanceAuditService
    AuditRecord — event_id, actor, action, resource, and timestamp
    only, deliberately dropping outcome/metadata/hash-chain fields
    that belong to that richer record, not to this narrower audit
    trail model.
    """

    event_id: str

    actor: str

    action: str

    resource: str

    timestamp: datetime

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id must not be empty")

        if not self.actor:
            raise ValueError("actor must not be empty")

        if not self.action:
            raise ValueError("action must not be empty")

        if not self.resource:
            raise ValueError("resource must not be empty")

        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def _from_record(cls, record: "AuditRecord") -> "AuditEvent":
        return cls(
            event_id=str(record.sequence),
            actor=record.actor,
            action=record.action,
            resource=record.resource,
            timestamp=record.occurred_at,
        )


@dataclass(frozen=True)
class AuditQuery:
    """
    Simple filter criteria for search() — every field optional; an
    unfiltered AuditQuery() matches every audit event.
    """

    actor: "str | None" = None

    action: "str | None" = None

    resource: "str | None" = None

    def to_dict(self) -> dict[str, object]:
        return {
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
        }


class DeploymentAuditService:
    """
    Records and queries immutable deployment governance audit events.

    Deliberately not a second, parallel audit log: this service is a
    read/query-oriented facade over the process-wide
    GovernanceAuditService's already-populated, hash-chained,
    append-only audit trail (see RECORDED_AUDIT_ACTION_CATEGORIES for
    what already flows into it). record() is available for a caller
    that wants to add an entry directly through this narrower
    AuditEvent model, but GovernanceAuditService itself remains the
    single owner of the underlying append-only log, its tamper-
    evidence, and every other write path into it.

    Compliance reporting and analytics integration are out of scope
    here — this service is intentionally scoped to recording and
    querying immutable audit events only.
    """

    def __init__(
        self, *, audit_service: "GovernanceAuditService | None" = None
    ) -> None:
        if audit_service is None:
            from .deployment_governance_audit import (
                GovernanceAuditService as _GovernanceAuditService,
            )

            audit_service = _GovernanceAuditService()

        self._audit_service = audit_service

    def record(
        self, *, actor: str, action: str, resource: str
    ) -> AuditEvent:
        """
        Record a new audit event and return its AuditEvent view.
        """

        record = self._audit_service.record(
            action=action, actor=actor, resource=resource,
            outcome="recorded",
        )

        return AuditEvent._from_record(record)

    def get(self, event_id: str) -> AuditEvent:
        """
        Return the audit event identified by event_id.

        Raises KeyError if event_id does not identify a recorded
        event.
        """

        try:
            sequence = int(event_id)

        except ValueError:
            raise KeyError(
                f"audit event '{event_id}' is not registered"
            ) from None

        try:
            record = self._audit_service.get(sequence)

        except LookupError:
            raise KeyError(
                f"audit event '{event_id}' is not registered"
            ) from None

        return AuditEvent._from_record(record)

    def list(self) -> "tuple[AuditEvent, ...]":
        """
        Return every currently recorded audit event, ordered
        deterministically by timestamp then event_id.
        """

        return self.search(AuditQuery())

    def search(self, query: AuditQuery) -> "tuple[AuditEvent, ...]":
        """
        Return every recorded audit event matching query (simple
        equality filtering on actor/action/resource — see AuditQuery),
        ordered deterministically by timestamp then event_id.
        """

        from .deployment_governance_audit import (
            AuditQuery as _UnderlyingAuditQuery,
        )

        limit = max(self._audit_service.size(), 1)

        records = self._audit_service.query(
            _UnderlyingAuditQuery(
                actor=query.actor, action=query.action,
                resource=query.resource, limit=limit,
            )
        )

        events = [AuditEvent._from_record(record) for record in records]

        return tuple(
            sorted(
                events,
                key=lambda event: (event.timestamp, event.event_id),
            )
        )

    def export(self) -> "tuple[dict[str, object], ...]":
        """
        Return every currently recorded audit event as a plain dict,
        suitable for serialization to an external log sink.
        Compliance-formatted reporting is out of scope here (a later
        commit's responsibility) — this is a straightforward dump.
        """

        return tuple(event.to_dict() for event in self.list())


def build_default_governance_audit_trail_service() -> (
    DeploymentAuditService
):
    """
    Build the process-wide deployment audit trail service, wired to
    the process-wide GovernanceAuditService — the same underlying
    audit trail DeploymentRBACEngine, DeploymentRolloutPolicyEngine,
    and DeploymentApprovalEngine already record into.
    """

    from .deployment_governance_audit import get_audit_service

    return DeploymentAuditService(audit_service=get_audit_service())


# Shared for the lifetime of the process, matching every other
# governance-security singleton in this module family.
_audit_trail_service = build_default_governance_audit_trail_service()


def get_audit_trail_service() -> DeploymentAuditService:
    """
    Return the process-wide deployment audit trail service.
    """

    return _audit_trail_service
