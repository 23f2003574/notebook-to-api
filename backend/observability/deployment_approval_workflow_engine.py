from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4


@dataclass
class DeploymentApprovalRequest:

    approval_id: str

    audit_id: str

    service_name: str

    environment: str

    artifact_digest: str

    validity_minutes: int

    requested_by: str

    status: str

    requested_at: str

    decided_by: Optional[str] = None

    decision_reason: Optional[str] = None

    decided_at: Optional[str] = None


@dataclass
class DeploymentGovernanceOutcome:

    decision: object

    audit_record: object

    approval_request: Optional[
        DeploymentApprovalRequest
    ]


class DeploymentApprovalWorkflowEngine:

    def request(
        self,
        audit_id: str,
        service_name: str,
        environment: str,
        artifact_digest: str,
        validity_minutes: int,
        requested_by: str
    ):

        if validity_minutes <= 0:

            raise ValueError(
                "approval validity duration "
                "must be greater than zero"
            )

        return DeploymentApprovalRequest(

            approval_id=
                str(uuid4()),

            audit_id=
                audit_id,

            service_name=
                service_name,

            environment=
                environment,

            artifact_digest=
                artifact_digest,

            validity_minutes=
                validity_minutes,

            requested_by=
                requested_by,

            status=
                "pending",

            requested_at=
                datetime
                .now(timezone.utc)
                .isoformat()
        )

    def approve(
        self,
        approval_request: DeploymentApprovalRequest,
        approved_by: str,
        reason: str
    ):

        self._ensure_pending(
            approval_request
        )

        approval_request.status = (
            "approved"
        )

        approval_request.decided_by = (
            approved_by
        )

        approval_request.decision_reason = (
            reason
        )

        approval_request.decided_at = (
            datetime
            .now(timezone.utc)
            .isoformat()
        )

        return approval_request

    def reject(
        self,
        approval_request: DeploymentApprovalRequest,
        rejected_by: str,
        reason: str
    ):

        self._ensure_pending(
            approval_request
        )

        approval_request.status = (
            "rejected"
        )

        approval_request.decided_by = (
            rejected_by
        )

        approval_request.decision_reason = (
            reason
        )

        approval_request.decided_at = (
            datetime
            .now(timezone.utc)
            .isoformat()
        )

        return approval_request

    def _ensure_pending(
        self,
        approval_request: DeploymentApprovalRequest
    ):

        if approval_request.status != "pending":

            raise ValueError(
                "deployment approval request "
                "has already been decided"
            )
