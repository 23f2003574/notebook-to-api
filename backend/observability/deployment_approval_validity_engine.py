from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List


@dataclass
class DeploymentApprovalValidityDecision:

    approval_id: str

    valid: bool

    expired: bool

    artifact_matches: bool

    environment_matches: bool

    reasons: List[str]


class DeploymentApprovalValidityEngine:

    def evaluate(
        self,
        approval_id: str,
        approval_status: str,
        approved_at: str,
        validity_minutes: int,
        approved_artifact_digest: str,
        deployment_artifact_digest: str,
        approved_environment: str,
        deployment_environment: str
    ):

        if validity_minutes <= 0:

            raise ValueError(
                "approval validity duration "
                "must be greater than zero"
            )

        reasons = []

        normalized_status = (
            approval_status
            .strip()
            .lower()
        )

        approval_time = (
            datetime
            .fromisoformat(
                approved_at
            )
        )

        if approval_time.tzinfo is None:

            approval_time = (
                approval_time
                .replace(
                    tzinfo=timezone.utc
                )
            )

        expires_at = (
            approval_time
            +
            timedelta(
                minutes=validity_minutes
            )
        )

        now = (
            datetime
            .now(timezone.utc)
        )

        expired = (
            now > expires_at
        )

        artifact_matches = (
            approved_artifact_digest
            ==
            deployment_artifact_digest
        )

        environment_matches = (
            approved_environment
            .strip()
            .lower()
            ==
            deployment_environment
            .strip()
            .lower()
        )

        if normalized_status != "approved":

            reasons.append(
                "deployment approval is not "
                "in the approved state"
            )

        if expired:

            reasons.append(
                "deployment approval has expired"
            )

        if not artifact_matches:

            reasons.append(
                "deployment artifact does not match "
                "the approved artifact"
            )

        if not environment_matches:

            reasons.append(
                "deployment environment does not match "
                "the approved environment"
            )

        valid = (
            normalized_status == "approved"
            and not expired
            and artifact_matches
            and environment_matches
        )

        if valid:

            reasons.append(
                "deployment approval remains valid "
                "for the current deployment context"
            )

        return DeploymentApprovalValidityDecision(

            approval_id=
                approval_id,

            valid=
                valid,

            expired=
                expired,

            artifact_matches=
                artifact_matches,

            environment_matches=
                environment_matches,

            reasons=
                reasons
        )
