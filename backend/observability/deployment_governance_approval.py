from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .deployment_governance_audit import GovernanceAuditService
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_rbac import DeploymentRBACEngine

# The lifecycle every approval request passes through: PENDING the
# instant it is created, then exactly one of the three terminal
# statuses — mirrors ROLLOUT_STATES' PENDING-then-one-terminal-state
# shape, minus RUNNING/PAUSED (an approval request has no in-progress
# state of its own to pass through).
APPROVAL_STATUSES: "tuple[str, ...]" = (
    "PENDING",
    "APPROVED",
    "REJECTED",
    "CANCELLED",
)

_TERMINAL_APPROVAL_STATUSES: "frozenset[str]" = frozenset(
    {"APPROVED", "REJECTED", "CANCELLED"}
)

# The permission approve()/reject() require of the deciding principal
# — DeploymentRBACEngine's own built-in permission vocabulary already
# includes this (see BUILT_IN_DEPLOYMENT_PERMISSIONS), so no new
# permission needed to introduce here.
APPROVAL_DECISION_PERMISSION = "deployment.approve"


@dataclass(frozen=True)
class ApprovalRequest:
    """
    One request for approval of a deployment operation. Immutable —
    approve()/reject()/cancel() produce a fresh ApprovalRequest rather
    than mutating this one, matching Rollout.
    """

    request_id: str

    deployment_id: str

    operation: str

    requester: str

    status: str

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request_id must not be empty")

        if not self.deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not self.operation:
            raise ValueError("operation must not be empty")

        if not self.requester:
            raise ValueError("requester must not be empty")

        if self.status not in APPROVAL_STATUSES:
            raise ValueError(
                f"status must be one of {APPROVAL_STATUSES}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "deployment_id": self.deployment_id,
            "operation": self.operation,
            "requester": self.requester,
            "status": self.status,
        }


@dataclass(frozen=True)
class ApprovalDecision:
    """
    An immutable record of one approve()/reject() call.
    """

    approver: str

    approved: bool

    reason: "str | None"

    decided_at: datetime

    def __post_init__(self) -> None:
        if not self.approver:
            raise ValueError("approver must not be empty")

        if self.decided_at.tzinfo is None:
            raise ValueError("decided_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "approver": self.approver,
            "approved": self.approved,
            "reason": self.reason,
            "decided_at": self.decided_at.isoformat(),
        }


class DeploymentApprovalEngine:
    """
    Tracks approval requests for deployment operations through their
    lifecycle: creation, approval, rejection, and cancellation.
    Rollout execution and compliance-policy integration (blocking a
    rollout on a pending request, or evaluating compliance rules
    before a request can even be created) are out of scope here —
    this engine only tracks approval identity, status, and who
    decided what.

    With an rbac_engine wired in, approve() and reject() enforce
    "authorized approvers only": the deciding principal must hold
    "deployment.approve" (APPROVAL_DECISION_PERMISSION), checked via
    DeploymentRBACEngine.require(), which raises PermissionError on
    denial. With no rbac_engine wired, any principal may decide —
    the same graceful-degradation default every other optional-
    dependency integration in this codebase uses. cancel() carries no
    such check: withdrawing one's own request is not a privileged
    decision the way approving or rejecting someone else's is.

    Thread-safe: every mutation of the request/decision registries is
    guarded by an internal lock. Lifecycle operations are idempotent
    for a request already in their target terminal status (reapplying
    approve() to an already-APPROVED request is a no-op returning the
    unchanged ApprovalRequest); applying one terminal operation to a
    request already settled in a *different* terminal status raises
    ValueError instead — there is no un-deciding a decision.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        rbac_engine: "DeploymentRBACEngine | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._requests: "dict[str, ApprovalRequest]" = {}

        self._decisions: "dict[str, ApprovalDecision]" = {}

        self._sequence: "dict[str, int]" = {}

        self._next_sequence = 1

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._rbac_engine = rbac_engine

        self._audit_service = audit_service

    def set_rbac_engine(
        self, rbac_engine: "DeploymentRBACEngine"
    ) -> None:
        """
        Wire rbac_engine in after construction, matching how
        build_default_governance_rbac_engine wires the process-wide
        RBAC engine into other engines' own singletons.
        """

        self._rbac_engine = rbac_engine

    def create_request(
        self, deployment_id: str, operation: str, requester: str
    ) -> ApprovalRequest:
        """
        Create a new PENDING approval request for operation on
        deployment_id.

        Raises ValueError if deployment_id, operation, or requester is
        empty, or if deployment_id already has an active (PENDING)
        approval request for the same operation — "one active
        approval per deployment operation". A new request for the
        same (deployment_id, operation) pair may be created once the
        prior one has left PENDING (approved, rejected, or
        cancelled).
        """

        if not deployment_id:
            raise ValueError("deployment_id must not be empty")

        if not operation:
            raise ValueError("operation must not be empty")

        if not requester:
            raise ValueError("requester must not be empty")

        with self._lock:
            for existing in self._requests.values():
                if (
                    existing.deployment_id == deployment_id
                    and existing.operation == operation
                    and existing.status == "PENDING"
                ):
                    raise ValueError(
                        f"deployment '{deployment_id}' already has an "
                        f"active approval request for operation "
                        f"'{operation}'"
                    )

            request = ApprovalRequest(
                request_id=str(uuid4()),
                deployment_id=deployment_id,
                operation=operation,
                requester=requester,
                status="PENDING",
            )

            self._requests[request.request_id] = request
            self._sequence[request.request_id] = self._next_sequence
            self._next_sequence += 1

        self._publish(
            "approval_requested", request.request_id, request.to_dict()
        )

        self._record_audit(
            action="approval_requested", actor=requester,
            resource=request.request_id, outcome="success",
            metadata=request.to_dict(),
        )

        return request

    def approve(
        self,
        request_id: str,
        approver: str,
        *,
        reason: "str | None" = None,
    ) -> ApprovalRequest:
        """
        Approve request_id as approver.

        Idempotent: a no-op returning the unchanged ApprovalRequest if
        already APPROVED (the RBAC check is not re-consulted in that
        case). Raises KeyError if request_id is not registered,
        ValueError if it is already REJECTED or CANCELLED, or —
        with an rbac_engine wired in — PermissionError if approver is
        not authorized for "deployment.approve".
        """

        return self._decide(
            request_id, approver, approved=True, reason=reason,
            to_status="APPROVED", event_type="approval_granted",
        )

    def reject(
        self,
        request_id: str,
        approver: str,
        *,
        reason: "str | None" = None,
    ) -> ApprovalRequest:
        """
        Reject request_id as approver.

        Idempotent: a no-op returning the unchanged ApprovalRequest if
        already REJECTED (the RBAC check is not re-consulted in that
        case). Raises KeyError if request_id is not registered,
        ValueError if it is already APPROVED or CANCELLED, or —
        with an rbac_engine wired in — PermissionError if approver is
        not authorized for "deployment.approve".
        """

        return self._decide(
            request_id, approver, approved=False, reason=reason,
            to_status="REJECTED", event_type="approval_rejected",
        )

    def cancel(self, request_id: str) -> ApprovalRequest:
        """
        Cancel request_id — withdrawing it, typically by its own
        requester. Carries no RBAC check: unlike approve()/reject(),
        cancelling is not a privileged decision over someone else's
        request in the way this engine models it.

        Idempotent: a no-op returning the unchanged ApprovalRequest if
        already CANCELLED. Raises KeyError if request_id is not
        registered, or ValueError if it is already APPROVED or
        REJECTED.
        """

        with self._lock:
            request = self._requests.get(request_id)

            if request is None:
                raise KeyError(
                    f"approval request '{request_id}' is not "
                    "registered"
                )

            if request.status == "CANCELLED":
                return request

            if request.status in _TERMINAL_APPROVAL_STATUSES:
                raise ValueError(
                    f"cannot cancel approval request '{request_id}' "
                    f"already in status '{request.status}'"
                )

            updated = replace(request, status="CANCELLED")
            self._requests[request_id] = updated

        self._publish(
            "approval_cancelled", request_id, updated.to_dict()
        )

        self._record_audit(
            action="approval_cancelled", actor=updated.requester,
            resource=request_id, outcome="success",
            metadata=updated.to_dict(),
        )

        return updated

    def get(self, request_id: str) -> ApprovalRequest:
        """
        Return request_id's current ApprovalRequest.

        Raises KeyError if request_id is not registered.
        """

        with self._lock:
            request = self._requests.get(request_id)

            if request is None:
                raise KeyError(
                    f"approval request '{request_id}' is not "
                    "registered"
                )

            return request

    def decision(self, request_id: str) -> ApprovalDecision:
        """
        Return request_id's recorded ApprovalDecision.

        Raises KeyError if request_id is not registered, or if it has
        not yet been approved or rejected (still PENDING, or
        cancelled without ever being decided).
        """

        with self._lock:
            decision = self._decisions.get(request_id)

            if decision is None:
                if request_id not in self._requests:
                    raise KeyError(
                        f"approval request '{request_id}' is not "
                        "registered"
                    )

                raise KeyError(
                    f"approval request '{request_id}' has not been "
                    "decided"
                )

            return decision

    def list_pending(self) -> "tuple[ApprovalRequest, ...]":
        """
        Return every PENDING approval request, ordered by creation
        order.
        """

        with self._lock:
            pending = [
                request
                for request in self._requests.values()
                if request.status == "PENDING"
            ]

            return tuple(
                sorted(
                    pending,
                    key=lambda request: self._sequence[
                        request.request_id
                    ],
                )
            )

    def list(self) -> "tuple[ApprovalRequest, ...]":
        """
        Return every approval request regardless of status, ordered
        by creation order.
        """

        with self._lock:
            requests = list(self._requests.values())

        return tuple(
            sorted(
                requests,
                key=lambda request: self._sequence[
                    request.request_id
                ],
            )
        )

    def clear(self) -> None:
        """
        Remove every registered approval request and decision.
        """

        with self._lock:
            self._requests.clear()
            self._decisions.clear()
            self._sequence.clear()
            self._next_sequence = 1

    def _decide(
        self,
        request_id: str,
        approver: str,
        *,
        approved: bool,
        reason: "str | None",
        to_status: str,
        event_type: str,
    ) -> ApprovalRequest:
        if not approver:
            raise ValueError("approver must not be empty")

        precheck = self._require_decidable(request_id, to_status)

        if precheck is not None:
            return precheck

        # Re-checked below under the lock after this call returns: an
        # RBAC check can take arbitrary time (a remote policy call, in
        # a future provider), during which another thread could have
        # already decided or cancelled this same request.
        if self._rbac_engine is not None:
            self._rbac_engine.require(
                approver, APPROVAL_DECISION_PERMISSION
            )

        now = self._clock()

        with self._lock:
            request = self._requests.get(request_id)

            if request is None:
                raise KeyError(
                    f"approval request '{request_id}' is not "
                    "registered"
                )

            if request.status == to_status:
                return request

            if request.status in _TERMINAL_APPROVAL_STATUSES:
                raise ValueError(
                    f"cannot decide approval request '{request_id}' "
                    f"already in status '{request.status}'"
                )

            updated = replace(request, status=to_status)
            self._requests[request_id] = updated

            self._decisions[request_id] = ApprovalDecision(
                approver=approver, approved=approved, reason=reason,
                decided_at=now,
            )

        self._publish(event_type, request_id, updated.to_dict())

        self._record_audit(
            action=event_type, actor=approver, resource=request_id,
            outcome="success",
            metadata={**updated.to_dict(), "reason": reason},
        )

        return updated

    def _require_decidable(
        self, request_id: str, to_status: str
    ) -> "ApprovalRequest | None":
        """
        Raise KeyError/ValueError if request_id cannot currently move
        to to_status, return the unchanged request if it is already
        there (the idempotent short-circuit), or None if it is
        PENDING and eligible to be decided.
        """

        with self._lock:
            request = self._requests.get(request_id)

            if request is None:
                raise KeyError(
                    f"approval request '{request_id}' is not "
                    "registered"
                )

            if request.status == to_status:
                return request

            if request.status in _TERMINAL_APPROVAL_STATUSES:
                raise ValueError(
                    f"cannot decide approval request '{request_id}' "
                    f"already in status '{request.status}'"
                )

            return None

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

    def _record_audit(
        self,
        *,
        action: str,
        actor: str,
        resource: str,
        outcome: str,
        metadata: "dict[str, Any] | None" = None,
    ) -> None:
        if self._audit_service is None:
            return

        self._audit_service.record(
            action=action, actor=actor, resource=resource,
            outcome=outcome, metadata=metadata or {},
        )


def build_default_governance_approval_engine() -> (
    DeploymentApprovalEngine
):
    """
    Build the process-wide deployment approval engine, wired to the
    process-wide governance event bus, RBAC engine, and audit service.
    """

    from .deployment_governance_audit import get_audit_service
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_rbac import get_rbac_engine

    return DeploymentApprovalEngine(
        event_bus=get_event_bus(), rbac_engine=get_rbac_engine(),
        audit_service=get_audit_service(),
    )


# Shared for the lifetime of the process: approval requests created
# through the API need to be decided/cancelled/listed identically by
# every caller, which a persistence runtime built fresh per request
# cannot provide on its own.
_approval_engine = build_default_governance_approval_engine()


def get_approval_engine() -> DeploymentApprovalEngine:
    """
    Return the process-wide deployment approval engine.
    """

    return _approval_engine
