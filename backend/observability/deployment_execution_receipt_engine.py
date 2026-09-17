from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List
from uuid import uuid4


@dataclass
class DeploymentExecutionReceipt:

    receipt_id: str

    token_id: str

    deployment_id: str

    artifact_digest: str

    environment: str

    executor_id: str

    execution_status: str

    started_at: str

    completed_at: str | None = None

    failure_reason: str | None = None


@dataclass
class DeploymentExecutionReceiptValidation:

    receipt_id: str

    valid: bool

    deployment_matches: bool

    artifact_matches: bool

    environment_matches: bool

    token_matches: bool

    reasons: List[str]


@dataclass
class DeploymentExecutionHandoff:

    authorization_token: object

    execution_receipt: DeploymentExecutionReceipt


class DeploymentExecutionReceiptEngine:

    def create(
        self,
        token,
        deployment_id: str,
        artifact_digest: str,
        environment: str,
        executor_id: str
    ):

        if not token.consumed:

            raise ValueError(
                "execution receipt cannot be created "
                "from an unconsumed authorization token"
            )

        if token.deployment_id != deployment_id:

            raise ValueError(
                "deployment does not match "
                "the consumed execution authorization"
            )

        if token.artifact_digest != artifact_digest:

            raise ValueError(
                "artifact does not match "
                "the consumed execution authorization"
            )

        normalized_environment = (
            environment
            .strip()
            .lower()
        )

        if token.environment != normalized_environment:

            raise ValueError(
                "environment does not match "
                "the consumed execution authorization"
            )

        normalized_executor_id = (
            executor_id
            .strip()
        )

        if not normalized_executor_id:

            raise ValueError(
                "executor identifier "
                "must not be empty"
            )

        return DeploymentExecutionReceipt(

            receipt_id=
                str(uuid4()),

            token_id=
                token.token_id,

            deployment_id=
                deployment_id,

            artifact_digest=
                artifact_digest,

            environment=
                normalized_environment,

            executor_id=
                normalized_executor_id,

            execution_status=
                "started",

            started_at=
                datetime
                .now(timezone.utc)
                .isoformat()
        )

    def validate(
        self,
        receipt: DeploymentExecutionReceipt,
        token,
        deployment_id: str,
        artifact_digest: str,
        environment: str
    ):

        reasons = []

        normalized_environment = (
            environment
            .strip()
            .lower()
        )

        token_matches = (
            receipt.token_id
            ==
            token.token_id
        )

        deployment_matches = (
            receipt.deployment_id
            ==
            deployment_id
        )

        artifact_matches = (
            receipt.artifact_digest
            ==
            artifact_digest
        )

        environment_matches = (
            receipt.environment
            ==
            normalized_environment
        )

        if not token_matches:

            reasons.append(
                "execution receipt does not reference "
                "the expected authorization token"
            )

        if not deployment_matches:

            reasons.append(
                "execution receipt does not match "
                "the expected deployment"
            )

        if not artifact_matches:

            reasons.append(
                "execution receipt does not match "
                "the expected artifact"
            )

        if not environment_matches:

            reasons.append(
                "execution receipt does not match "
                "the expected environment"
            )

        valid = (
            token_matches
            and deployment_matches
            and artifact_matches
            and environment_matches
        )

        if valid:

            reasons.append(
                "execution receipt matches "
                "the authorized deployment context"
            )

        return DeploymentExecutionReceiptValidation(

            receipt_id=
                receipt.receipt_id,

            valid=
                valid,

            deployment_matches=
                deployment_matches,

            artifact_matches=
                artifact_matches,

            environment_matches=
                environment_matches,

            token_matches=
                token_matches,

            reasons=
                reasons
        )

    def mark_succeeded(
        self,
        receipt: DeploymentExecutionReceipt
    ):

        self._ensure_started(
            receipt
        )

        receipt.execution_status = (
            "succeeded"
        )

        receipt.completed_at = (
            datetime
            .now(timezone.utc)
            .isoformat()
        )

        return receipt

    def mark_failed(
        self,
        receipt: DeploymentExecutionReceipt,
        failure_reason: str
    ):

        self._ensure_started(
            receipt
        )

        receipt.execution_status = (
            "failed"
        )

        receipt.failure_reason = (
            failure_reason
        )

        receipt.completed_at = (
            datetime
            .now(timezone.utc)
            .isoformat()
        )

        return receipt

    def _ensure_started(
        self,
        receipt: DeploymentExecutionReceipt
    ):

        if receipt.execution_status != "started":

            raise ValueError(
                "deployment execution receipt "
                "has already reached a terminal state"
            )
