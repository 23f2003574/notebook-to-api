from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .deployment_governance_approval import DeploymentApprovalEngine
    from .deployment_governance_artifact_integrity import (
        DeploymentIntegrityVerifier,
    )
    from .deployment_governance_audit import GovernanceAuditService
    from .deployment_governance_compliance import (
        DeploymentComplianceEngine,
    )
    from .deployment_governance_event_bus import (
        GovernanceEvent,
        GovernanceEventBus,
    )
    from .deployment_governance_rollback import DeploymentRollbackEngine
    from .deployment_governance_rollout_manager import (
        DeploymentRolloutManager,
    )
    from .deployment_governance_risk import DeploymentRiskEngine
    from .deployment_governance_security_scanner import (
        DeploymentSecurityScanner,
    )

SEVERITY_LEVELS: "tuple[str, ...]" = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

_SEVERITY_RANK: "dict[str, int]" = {
    level: rank for rank, level in enumerate(SEVERITY_LEVELS)
}

# The lifecycle every incident passes through: OPEN the instant it is
# created, ASSIGNED once someone takes ownership (assign() is
# idempotent and optional — resolve() works directly from OPEN too),
# then RESOLVED — the one terminal status.
INCIDENT_STATUSES: "tuple[str, ...]" = ("OPEN", "ASSIGNED", "RESOLVED")

# The built-in triggers this engine evaluates via detect(), each
# scoped to a fixed severity — documented vocabulary matching how
# BUILT_IN_ROLLOUT_POLICIES documents a vocabulary without enforcing
# it as a closed set (a caller can still create()/resolve() incidents
# manually for any other reason). Every trigger degrades to a
# context-only fallback when its optional engine is not wired — the
# same graceful-degradation contract every other optional-dependency
# integration in this codebase follows.
DEFAULT_TRIGGERS: "tuple[str, ...]" = (
    "critical_security_finding",
    "integrity_verification_failure",
    "repeated_authentication_failures",
    "compliance_violation",
    "critical_risk_score",
)

_TRIGGER_SEVERITY: "dict[str, str]" = {
    "critical_security_finding": "CRITICAL",
    "integrity_verification_failure": "CRITICAL",
    "repeated_authentication_failures": "HIGH",
    "compliance_violation": "MEDIUM",
    "critical_risk_score": "CRITICAL",
}

# The response actions this engine can execute on incident creation,
# and which of them fire at each severity — "deterministic action
# execution": the same severity always executes the same fixed set of
# actions, in this same order.
DEFAULT_RESPONSE_ACTIONS: "tuple[str, ...]" = (
    "Flag Deployment",
    "Pause Rollout",
    "Require Manual Approval",
    "Trigger Rollback",
    "Record Audit Event",
)

_SEVERITY_ACTIONS: "dict[str, tuple[str, ...]]" = {
    "CRITICAL": DEFAULT_RESPONSE_ACTIONS,
    "HIGH": (
        "Flag Deployment", "Require Manual Approval",
        "Record Audit Event",
    ),
    "MEDIUM": ("Flag Deployment", "Record Audit Event"),
    "LOW": ("Record Audit Event",),
}

_AUTH_FAILURE_THRESHOLD = 3


@dataclass(frozen=True)
class DeploymentIncident:
    """
    One immutable, point-in-time record of a detected or manually
    created security incident.
    """

    incident_id: str

    severity: str

    status: str

    source: str

    def __post_init__(self) -> None:
        if not self.incident_id:
            raise ValueError("incident_id must not be empty")

        if self.severity not in SEVERITY_LEVELS:
            raise ValueError(
                f"severity must be one of {SEVERITY_LEVELS}"
            )

        if self.status not in INCIDENT_STATUSES:
            raise ValueError(
                f"status must be one of {INCIDENT_STATUSES}"
            )

        if not self.source:
            raise ValueError("source must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "incident_id": self.incident_id,
            "severity": self.severity,
            "status": self.status,
            "source": self.source,
        }


@dataclass(frozen=True)
class IncidentAction:
    """
    One immutable record of a response action this engine attempted
    on an incident's creation, and whether it actually executed
    (versus being skipped because its underlying dependency was not
    wired, or failing best-effort).
    """

    action: str

    executed: bool

    def to_dict(self) -> dict[str, object]:
        return {"action": self.action, "executed": self.executed}


@dataclass(frozen=True)
class IncidentSummary:
    """
    An immutable, point-in-time aggregate over every incident this
    engine has ever recorded.
    """

    total_incidents: int

    open_incidents: int

    resolved_incidents: int

    critical_incidents: int

    def __post_init__(self) -> None:
        if self.open_incidents + self.resolved_incidents != (
            self.total_incidents
        ):
            raise ValueError(
                "open_incidents + resolved_incidents must equal "
                "total_incidents"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "total_incidents": self.total_incidents,
            "open_incidents": self.open_incidents,
            "resolved_incidents": self.resolved_incidents,
            "critical_incidents": self.critical_incidents,
        }


class DeploymentIncidentResponseEngine:
    """
    Detects security incidents from governance events and orchestrates
    a fixed set of response actions for them. detect() evaluates the
    five DEFAULT_TRIGGERS against one target identifier (a deployment_
    id for every trigger except "repeated_authentication_failures",
    which is scoped to a principal — the same target_id parameter is
    checked against all five regardless, so calling detect() with a
    deployment_id simply never matches that one trigger, and vice
    versa); create() is the manual entry point the same triggers (and
    any other caller) funnel through.

    "One active incident per trigger source": create()/detect() raise
    ValueError for a source that already has a non-RESOLVED incident
    — unless the new severity outranks the existing incident's, in
    which case it is escalated in place (same incident_id, updated
    severity) and an "incident_escalated" event is published instead.

    Response actions (DEFAULT_RESPONSE_ACTIONS) execute once, on
    creation (not escalation), per _SEVERITY_ACTIONS — CRITICAL runs
    all five, in order; each degrades to executed=False when its
    underlying optional dependency is not wired, or on failure,
    without raising. "Repeated authentication failures" is detected
    reactively: with an event_bus wired, this engine subscribes to
    "authentication_failed" (counting occurrences per principal,
    resetting on that principal's next "authentication_succeeded")
    rather than being told a count directly — the literal "detects
    incidents from governance events" this engine is named for.

    Notifications, dashboards, and reporting that consume incidents
    are out of scope here — this engine only detects, creates, and
    tracks their lifecycle.

    Thread-safe: the incident registry, action log, and auth-failure
    counters are guarded by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        security_scanner: "DeploymentSecurityScanner | None" = None,
        integrity_verifier: "DeploymentIntegrityVerifier | None" = None,
        compliance_engine: "DeploymentComplianceEngine | None" = None,
        risk_engine: "DeploymentRiskEngine | None" = None,
        approval_engine: "DeploymentApprovalEngine | None" = None,
        rollout_manager: "DeploymentRolloutManager | None" = None,
        rollback_engine: "DeploymentRollbackEngine | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._incidents: "dict[str, DeploymentIncident]" = {}

        self._active_by_source: "dict[str, str]" = {}

        self._actions: "dict[str, tuple[IncidentAction, ...]]" = {}

        self._assignees: "dict[str, str]" = {}

        self._sequence: "dict[str, int]" = {}

        self._next_sequence = 1

        self._flagged: "set[str]" = set()

        self._auth_failure_counts: "dict[str, int]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._security_scanner = security_scanner

        self._integrity_verifier = integrity_verifier

        self._compliance_engine = compliance_engine

        self._risk_engine = risk_engine

        self._approval_engine = approval_engine

        self._rollout_manager = rollout_manager

        self._rollback_engine = rollback_engine

        self._audit_service = audit_service

        if self._event_bus is not None:
            self._event_bus.subscribe(
                "authentication_failed", self._on_authentication_failed
            )
            self._event_bus.subscribe(
                "authentication_succeeded",
                self._on_authentication_succeeded,
            )

    def detect(
        self, target_id: str, context: "dict[str, Any] | None" = None
    ) -> "tuple[DeploymentIncident, ...]":
        """
        Evaluate every DEFAULT_TRIGGERS against target_id, in trigger
        order, creating (or escalating) an incident for each currently
        triggered condition — "source '<trigger>:<target_id>'" — and
        returning every incident that is currently active as a result
        (whether newly created, escalated, or already active from an
        earlier detect() call).

        Raises ValueError if target_id is empty.
        """

        if not target_id:
            raise ValueError("target_id must not be empty")

        context = context or {}
        results = []

        for trigger_name in DEFAULT_TRIGGERS:
            check = self._built_in_triggers()[trigger_name]

            if not check(target_id, context):
                continue

            source = f"{trigger_name}:{target_id}"
            severity = _TRIGGER_SEVERITY[trigger_name]

            try:
                incident = self.create(source, severity)

            except ValueError:
                incident = self._active_incident_for_source(source)

            if incident is not None:
                results.append(incident)

                self._publish(
                    "incident_detected", target_id, incident.to_dict()
                )

        return tuple(results)

    def create(
        self, source: str, severity: str, *, status: str = "OPEN"
    ) -> DeploymentIncident:
        """
        Create a new incident for source at severity.

        Raises ValueError if source, severity, or status is invalid,
        or if source already has an active (non-RESOLVED) incident at
        the same or higher severity — "one active incident per
        trigger source". If the existing incident's severity is
        lower, it is escalated in place instead (same incident_id,
        updated severity) and returned; no new incident, and no
        response actions, are created for an escalation.
        """

        if not source:
            raise ValueError("source must not be empty")

        if severity not in SEVERITY_LEVELS:
            raise ValueError(f"severity must be one of {SEVERITY_LEVELS}")

        if status not in INCIDENT_STATUSES:
            raise ValueError(f"status must be one of {INCIDENT_STATUSES}")

        existing = self._active_incident_for_source(source)

        if existing is not None:
            if _SEVERITY_RANK[severity] > _SEVERITY_RANK[existing.severity]:
                with self._lock:
                    escalated = replace(existing, severity=severity)
                    self._incidents[escalated.incident_id] = escalated

                self._publish(
                    "incident_escalated", escalated.incident_id,
                    escalated.to_dict(),
                )

                return escalated

            raise ValueError(
                f"source '{source}' already has an active incident"
            )

        incident = DeploymentIncident(
            incident_id=str(uuid4()), severity=severity, status=status,
            source=source,
        )

        with self._lock:
            self._incidents[incident.incident_id] = incident
            self._active_by_source[source] = incident.incident_id
            self._sequence[incident.incident_id] = self._next_sequence
            self._next_sequence += 1

        actions = self._execute_response_actions(incident)

        with self._lock:
            self._actions[incident.incident_id] = actions

        self._publish(
            "incident_created", incident.incident_id, incident.to_dict()
        )

        return incident

    def assign(self, incident_id: str, assignee: str) -> DeploymentIncident:
        """
        Assign incident_id to assignee, transitioning it to ASSIGNED.

        Idempotent: a no-op status transition (assignee is still
        updated) if already ASSIGNED. Raises KeyError if incident_id
        is not registered, ValueError if assignee is empty or the
        incident is already RESOLVED.
        """

        if not assignee:
            raise ValueError("assignee must not be empty")

        with self._lock:
            incident = self._incidents.get(incident_id)

            if incident is None:
                raise KeyError(
                    f"incident '{incident_id}' is not registered"
                )

            if incident.status == "RESOLVED":
                raise ValueError(
                    f"cannot assign incident '{incident_id}' already "
                    "resolved"
                )

            self._assignees[incident_id] = assignee

            if incident.status == "ASSIGNED":
                return incident

            updated = replace(incident, status="ASSIGNED")
            self._incidents[incident_id] = updated

            return updated

    def resolve(
        self, incident_id: str, *, reason: "str | None" = None
    ) -> DeploymentIncident:
        """
        Resolve incident_id, transitioning it to RESOLVED and freeing
        its source for a new incident.

        Idempotent: a no-op if already RESOLVED. Raises KeyError if
        incident_id is not registered.
        """

        with self._lock:
            incident = self._incidents.get(incident_id)

            if incident is None:
                raise KeyError(
                    f"incident '{incident_id}' is not registered"
                )

            if incident.status == "RESOLVED":
                return incident

            updated = replace(incident, status="RESOLVED")
            self._incidents[incident_id] = updated
            self._active_by_source.pop(updated.source, None)

        self._publish(
            "incident_resolved", incident_id, updated.to_dict()
        )

        return updated

    def get(self, incident_id: str) -> DeploymentIncident:
        """
        Return incident_id's current DeploymentIncident.

        Raises KeyError if incident_id is not registered.
        """

        with self._lock:
            incident = self._incidents.get(incident_id)

            if incident is None:
                raise KeyError(
                    f"incident '{incident_id}' is not registered"
                )

            return incident

    def actions(self, incident_id: str) -> "tuple[IncidentAction, ...]":
        """
        Return the response actions executed when incident_id was
        created (empty for an escalation-only update — see create()).

        Raises KeyError if incident_id is not registered.
        """

        with self._lock:
            if incident_id not in self._incidents:
                raise KeyError(
                    f"incident '{incident_id}' is not registered"
                )

            return self._actions.get(incident_id, ())

    def history(self) -> "tuple[DeploymentIncident, ...]":
        """
        Return every incident ever recorded, regardless of status,
        ordered by creation order — "immutable incident history".
        """

        with self._lock:
            incidents = list(self._incidents.values())

        return tuple(
            sorted(
                incidents,
                key=lambda incident: self._sequence[
                    incident.incident_id
                ],
            )
        )

    def summary(self) -> IncidentSummary:
        """
        Return a point-in-time aggregate over every incident recorded
        so far.
        """

        incidents = self.history()

        resolved = sum(
            1 for incident in incidents if incident.status == "RESOLVED"
        )

        return IncidentSummary(
            total_incidents=len(incidents),
            open_incidents=len(incidents) - resolved,
            resolved_incidents=resolved,
            critical_incidents=sum(
                1
                for incident in incidents
                if incident.severity == "CRITICAL"
            ),
        )

    def clear(self) -> None:
        """
        Remove every recorded incident, action log entry, and
        auth-failure counter.
        """

        with self._lock:
            self._incidents.clear()
            self._active_by_source.clear()
            self._actions.clear()
            self._assignees.clear()
            self._sequence.clear()
            self._next_sequence = 1
            self._flagged.clear()
            self._auth_failure_counts.clear()

    def _active_incident_for_source(
        self, source: str
    ) -> "DeploymentIncident | None":
        with self._lock:
            incident_id = self._active_by_source.get(source)

            return (
                self._incidents.get(incident_id)
                if incident_id is not None
                else None
            )

    def _built_in_triggers(
        self,
    ) -> "dict[str, Callable[[str, dict[str, Any]], bool]]":
        return {
            "critical_security_finding": (
                self._trigger_critical_security_finding
            ),
            "integrity_verification_failure": (
                self._trigger_integrity_verification_failure
            ),
            "repeated_authentication_failures": (
                self._trigger_repeated_authentication_failures
            ),
            "compliance_violation": self._trigger_compliance_violation,
            "critical_risk_score": self._trigger_critical_risk_score,
        }

    def _trigger_critical_security_finding(
        self, target_id: str, context: "dict[str, Any]"
    ) -> bool:
        if self._security_scanner is not None:
            return self._security_scanner.has_critical_finding(target_id)

        return bool(context.get("critical_security_finding", False))

    def _trigger_integrity_verification_failure(
        self, target_id: str, context: "dict[str, Any]"
    ) -> bool:
        if self._integrity_verifier is not None:
            return self._integrity_verifier.latest_failed(target_id)

        return bool(context.get("integrity_verification_failure", False))

    def _trigger_repeated_authentication_failures(
        self, target_id: str, context: "dict[str, Any]"
    ) -> bool:
        with self._lock:
            count = self._auth_failure_counts.get(target_id, 0)

        if count >= _AUTH_FAILURE_THRESHOLD:
            return True

        return context.get("authentication_failures", 0) >= (
            _AUTH_FAILURE_THRESHOLD
        )

    def _trigger_compliance_violation(
        self, target_id: str, context: "dict[str, Any]"
    ) -> bool:
        if self._compliance_engine is not None:
            return self._compliance_engine.violation_count(
                target_id, context
            ) > 0

        return bool(context.get("compliance_violation", False))

    def _trigger_critical_risk_score(
        self, target_id: str, context: "dict[str, Any]"
    ) -> bool:
        if self._risk_engine is not None:
            try:
                return self._risk_engine.latest(target_id).level == (
                    "CRITICAL"
                )

            except KeyError:
                return False

        return bool(context.get("critical_risk_score", False))

    def _execute_response_actions(
        self, incident: DeploymentIncident
    ) -> "tuple[IncidentAction, ...]":
        target_id = (
            incident.source.split(":", 1)[1]
            if ":" in incident.source
            else incident.source
        )

        return tuple(
            IncidentAction(
                action=action_name,
                executed=self._execute_action(
                    action_name, incident, target_id
                ),
            )
            for action_name in _SEVERITY_ACTIONS.get(
                incident.severity, ()
            )
        )

    def _execute_action(
        self,
        action_name: str,
        incident: DeploymentIncident,
        target_id: str,
    ) -> bool:
        if action_name == "Flag Deployment":
            with self._lock:
                self._flagged.add(target_id)

            return True

        if action_name == "Record Audit Event":
            if self._audit_service is None:
                return False

            self._audit_service.record(
                action="incident_response", actor="system",
                resource=incident.incident_id, outcome="recorded",
                metadata=incident.to_dict(),
            )

            return True

        if action_name == "Require Manual Approval":
            if self._approval_engine is None:
                return False

            try:
                self._approval_engine.create_request(
                    target_id, "incident_response", "system",
                )

            except ValueError:
                return False

            return True

        if action_name == "Pause Rollout":
            if self._rollout_manager is None:
                return False

            try:
                for rollout in self._rollout_manager.list():
                    if (
                        rollout.deployment_id == target_id
                        and rollout.state == "RUNNING"
                    ):
                        self._rollout_manager.pause(rollout.rollout_id)

                        return True

                return False

            except (KeyError, ValueError):
                return False

        if action_name == "Trigger Rollback":
            if self._rollback_engine is None:
                return False

            try:
                self._rollback_engine.create_plan(target_id)
                self._rollback_engine.execute(target_id)

                return True

            except (KeyError, ValueError):
                return False

        return False

    def is_flagged(self, target_id: str) -> bool:
        """
        Return whether target_id was flagged by a "Flag Deployment"
        response action.
        """

        with self._lock:
            return target_id in self._flagged

    def _on_authentication_failed(
        self, event: "GovernanceEvent"
    ) -> None:
        principal = event.source

        with self._lock:
            self._auth_failure_counts[principal] = (
                self._auth_failure_counts.get(principal, 0) + 1
            )

    def _on_authentication_succeeded(
        self, event: "GovernanceEvent"
    ) -> None:
        identity = event.payload.get("identity") or {}
        principal = identity.get("principal")

        if not principal:
            return

        with self._lock:
            self._auth_failure_counts.pop(principal, None)

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


def build_default_governance_incident_response_engine() -> (
    DeploymentIncidentResponseEngine
):
    """
    Build the process-wide deployment incident response engine, wired
    to the process-wide governance event bus, security scanner,
    integrity verifier, compliance engine, risk engine, approval
    engine, rollout manager, rollback engine, and audit service.
    """

    from .deployment_governance_approval import get_approval_engine
    from .deployment_governance_artifact_integrity import (
        get_artifact_integrity_verifier,
    )
    from .deployment_governance_audit import get_audit_service
    from .deployment_governance_compliance import get_compliance_engine
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_rollback import get_rollback_engine
    from .deployment_governance_rollout_manager import (
        get_rollout_manager,
    )
    from .deployment_governance_risk import get_risk_engine
    from .deployment_governance_security_scanner import (
        get_security_scanner,
    )

    return DeploymentIncidentResponseEngine(
        event_bus=get_event_bus(),
        security_scanner=get_security_scanner(),
        integrity_verifier=get_artifact_integrity_verifier(),
        compliance_engine=get_compliance_engine(),
        risk_engine=get_risk_engine(),
        approval_engine=get_approval_engine(),
        rollout_manager=get_rollout_manager(),
        rollback_engine=get_rollback_engine(),
        audit_service=get_audit_service(),
    )


# Shared for the lifetime of the process: incidents created through
# the API need to be resolved/listed identically by every caller, and
# the auth-failure counters only mean anything if they observe every
# "authentication_failed" event the running process actually
# publishes, which a persistence runtime built fresh per request
# cannot provide on its own.
_incident_response_engine = (
    build_default_governance_incident_response_engine()
)


def get_incident_response_engine() -> DeploymentIncidentResponseEngine:
    """
    Return the process-wide deployment incident response engine.
    """

    return _incident_response_engine
