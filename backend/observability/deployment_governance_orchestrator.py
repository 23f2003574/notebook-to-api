from dataclasses import dataclass
from typing import Optional


@dataclass
class DeploymentGovernanceInitiation:

    trace: object

    policy_decision: object

    audit_record: object

    approval_request: Optional[object]


@dataclass
class DeploymentGovernanceApprovalResult:

    trace: object

    authorization_decision: object

    approval_request: object


@dataclass
class DeploymentGovernanceExecutionPreparation:

    trace: object

    policy_decision: object

    approval_validity: Optional[object]

    eligibility: object

    authorization_token: Optional[object]


@dataclass
class DeploymentGovernanceExecutionHandoffResult:

    trace: object

    authorization_token: object

    execution_receipt: object


class DeploymentGovernanceOrchestrator:

    def __init__(
        self,
        trace_engine,
        trace_registry,
        policy_engine,
        audit_engine,
        approval_workflow_engine,
        approval_authorization_engine,
        approval_validity_engine,
        execution_eligibility_engine,
        execution_authorization_engine,
        execution_receipt_engine
    ):

        self.trace_engine = (
            trace_engine
        )

        self.trace_registry = (
            trace_registry
        )

        self.policy_engine = (
            policy_engine
        )

        self.audit_engine = (
            audit_engine
        )

        self.approval_workflow_engine = (
            approval_workflow_engine
        )

        self.approval_authorization_engine = (
            approval_authorization_engine
        )

        self.approval_validity_engine = (
            approval_validity_engine
        )

        self.execution_eligibility_engine = (
            execution_eligibility_engine
        )

        self.execution_authorization_engine = (
            execution_authorization_engine
        )

        self.execution_receipt_engine = (
            execution_receipt_engine
        )

    def initiate(
        self,
        deployment_id: str,
        service_name: str,
        environment: str,
        artifact_digest: str,
        risk_level: str,
        error_budget_exhausted: bool,
        burn_rate: float,
        requested_by: str,
        approval_validity_minutes: int = 60
    ):

        trace = (
            self
            .trace_engine
            .create(
                deployment_id,
                service_name,
                environment,
                artifact_digest
            )
        )

        self.trace_registry.register(
            trace
        )

        policy_decision = (
            self
            .policy_engine
            .evaluate(
                service_name,
                environment,
                risk_level,
                error_budget_exhausted,
                burn_rate
            )
        )

        audit_record = (
            self
            .audit_engine
            .record(
                service_name,
                environment,
                policy_decision.decision,
                policy_decision.reasons
            )
        )

        self.trace_engine.record_event(
            trace,
            "policy_evaluation",
            policy_decision.decision,
            "; ".join(
                policy_decision.reasons
            ),
            audit_record.audit_id
        )

        approval_request = None

        if (
            policy_decision.decision
            ==
            "require_approval"
        ):

            approval_request = (
                self
                .approval_workflow_engine
                .request(
                    audit_record.audit_id,
                    service_name,
                    environment,
                    artifact_digest,
                    approval_validity_minutes,
                    requested_by
                )
            )

            self.trace_engine.record_event(
                trace,
                "approval_request",
                approval_request.status,
                (
                    "deployment approval "
                    "request created"
                ),
                approval_request.approval_id
            )

        return DeploymentGovernanceInitiation(

            trace=
                trace,

            policy_decision=
                policy_decision,

            audit_record=
                audit_record,

            approval_request=
                approval_request
        )

    def decide_approval(
        self,
        trace,
        approval_request,
        actor_id: str,
        actor_roles,
        decision: str,
        reason: str
    ):

        authorization_decision = (
            self
            .approval_authorization_engine
            .authorize(
                actor_id,
                actor_roles,
                approval_request.environment,
                decision
            )
        )

        authorization_status = (
            "authorized"
            if authorization_decision.authorized
            else "denied"
        )

        self.trace_engine.record_event(
            trace,
            "approval_authorization",
            authorization_status,
            "; ".join(
                authorization_decision.reasons
            ),
            approval_request.approval_id
        )

        if not authorization_decision.authorized:

            return (
                DeploymentGovernanceApprovalResult(

                    trace=
                        trace,

                    authorization_decision=
                        authorization_decision,

                    approval_request=
                        approval_request
                )
            )

        normalized_decision = (
            decision
            .strip()
            .lower()
        )

        if normalized_decision == "approve":

            approval_request = (
                self
                .approval_workflow_engine
                .approve(
                    approval_request,
                    actor_id,
                    reason
                )
            )

        elif normalized_decision == "reject":

            approval_request = (
                self
                .approval_workflow_engine
                .reject(
                    approval_request,
                    actor_id,
                    reason
                )
            )

        else:

            raise ValueError(
                "approval decision must be "
                "'approve' or 'reject'"
            )

        self.trace_engine.record_event(
            trace,
            "approval_decision",
            approval_request.status,
            reason,
            approval_request.approval_id
        )

        return DeploymentGovernanceApprovalResult(

            trace=
                trace,

            authorization_decision=
                authorization_decision,

            approval_request=
                approval_request
        )

    def _ensure_trace_context(
        self,
        trace,
        deployment_id: str,
        artifact_digest: str,
        environment: str
    ):

        valid = (
            self
            .trace_engine
            .validate_context(
                trace,
                deployment_id,
                artifact_digest,
                environment
            )
        )

        if not valid:

            raise ValueError(
                "deployment governance trace "
                "does not match deployment context"
            )

    def prepare_execution(
        self,
        trace,
        service_name: str,
        environment: str,
        risk_level: str,
        error_budget_exhausted: bool,
        burn_rate: float,
        active_incidents: int,
        deployment_id: str,
        artifact_digest: str,
        approval_request=None,
        authorization_validity_minutes: int = 5
    ):

        self._ensure_trace_context(
            trace,
            deployment_id,
            artifact_digest,
            environment
        )

        if approval_request is not None:

            if (
                approval_request.service_name
                !=
                service_name
            ):

                raise ValueError(
                    "approval request service does not "
                    "match deployment service"
                )

            if (
                approval_request.environment
                .strip()
                .lower()
                !=
                environment
                .strip()
                .lower()
            ):

                raise ValueError(
                    "approval request environment does not "
                    "match deployment environment"
                )

        policy_decision = (
            self
            .policy_engine
            .evaluate(
                service_name,
                environment,
                risk_level,
                error_budget_exhausted,
                burn_rate
            )
        )

        approval_required = (
            policy_decision.decision
            ==
            "require_approval"
        )

        approval_validity = None

        approval_valid = False

        if approval_request is not None:

            approved_at = (
                approval_request.decided_at
                or
                approval_request.requested_at
            )

            approval_validity = (
                self
                .approval_validity_engine
                .evaluate(
                    approval_request.approval_id,
                    approval_request.status,
                    approved_at,
                    approval_request.validity_minutes,
                    approval_request.artifact_digest,
                    artifact_digest,
                    approval_request.environment,
                    environment
                )
            )

            approval_valid = (
                approval_validity.valid
            )

            self.trace_engine.record_event(
                trace,
                "approval_validity",
                (
                    "valid"
                    if approval_valid
                    else "invalid"
                ),
                "; ".join(
                    approval_validity.reasons
                ),
                approval_request.approval_id
            )

        eligibility = (
            self
            .execution_eligibility_engine
            .evaluate(
                service_name,
                environment,
                policy_decision.decision,
                approval_required,
                approval_valid,
                error_budget_exhausted,
                burn_rate,
                active_incidents
            )
        )

        self.trace_engine.record_event(
            trace,
            "execution_eligibility",
            eligibility.decision,
            "; ".join(
                eligibility.reasons
            )
        )

        authorization_token = None

        if eligibility.eligible:

            authorization_token = (
                self
                .execution_authorization_engine
                .issue(
                    deployment_id,
                    artifact_digest,
                    environment,
                    eligibility.decision,
                    authorization_validity_minutes
                )
            )

            self.trace_engine.record_event(
                trace,
                "execution_authorization",
                "issued",
                (
                    "single-use deployment execution "
                    "authorization issued"
                ),
                authorization_token.token_id
            )

        return (
            DeploymentGovernanceExecutionPreparation(

                trace=
                    trace,

                policy_decision=
                    policy_decision,

                approval_validity=
                    approval_validity,

                eligibility=
                    eligibility,

                authorization_token=
                    authorization_token
            )
        )

    def handoff_execution(
        self,
        trace,
        token,
        deployment_id: str,
        artifact_digest: str,
        environment: str,
        executor_id: str
    ):

        self._ensure_trace_context(
            trace,
            deployment_id,
            artifact_digest,
            environment
        )

        consumed_token = (
            self
            .execution_authorization_engine
            .consume(
                token,
                deployment_id,
                artifact_digest,
                environment
            )
        )

        self.trace_engine.record_event(
            trace,
            "execution_authorization",
            "consumed",
            (
                "deployment execution "
                "authorization consumed"
            ),
            consumed_token.token_id
        )

        execution_receipt = (
            self
            .execution_receipt_engine
            .create(
                consumed_token,
                deployment_id,
                artifact_digest,
                environment,
                executor_id
            )
        )

        self.trace_engine.record_event(
            trace,
            "deployment_execution",
            execution_receipt.execution_status,
            (
                "deployment execution "
                "accepted by executor"
            ),
            execution_receipt.receipt_id
        )

        return (
            DeploymentGovernanceExecutionHandoffResult(

                trace=
                    trace,

                authorization_token=
                    consumed_token,

                execution_receipt=
                    execution_receipt
            )
        )

    def complete_execution(
        self,
        trace,
        receipt,
        succeeded: bool,
        failure_reason: str | None = None
    ):

        if succeeded:

            receipt = (
                self
                .execution_receipt_engine
                .mark_succeeded(
                    receipt
                )
            )

        else:

            if not failure_reason:

                raise ValueError(
                    "failure reason is required "
                    "for failed deployment execution"
                )

            receipt = (
                self
                .execution_receipt_engine
                .mark_failed(
                    receipt,
                    failure_reason
                )
            )

        details = (
            "deployment execution completed successfully"
            if succeeded
            else failure_reason
        )

        self.trace_engine.record_event(
            trace,
            "deployment_execution",
            receipt.execution_status,
            details,
            receipt.receipt_id
        )

        return receipt
