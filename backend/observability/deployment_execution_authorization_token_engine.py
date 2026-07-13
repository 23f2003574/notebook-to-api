from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List
from uuid import uuid4


@dataclass
class DeploymentExecutionAuthorizationToken:

    token_id: str

    deployment_id: str

    artifact_digest: str

    environment: str

    issued_at: str

    expires_at: str

    consumed: bool = False

    consumed_at: str | None = None


@dataclass
class DeploymentExecutionAuthorizationValidation:

    token_id: str

    valid: bool

    expired: bool

    consumed: bool

    deployment_matches: bool

    artifact_matches: bool

    environment_matches: bool

    reasons: List[str]


@dataclass
class AuthorizedDeploymentExecution:

    readiness: object

    authorization_token: (
        DeploymentExecutionAuthorizationToken
        | None
    )


class DeploymentExecutionAuthorizationTokenEngine:

    def issue(
        self,
        deployment_id: str,
        artifact_digest: str,
        environment: str,
        eligibility_decision: str,
        validity_minutes: int = 5
    ):

        normalized_decision = (
            eligibility_decision
            .strip()
            .lower()
        )

        if normalized_decision != "execute":

            raise ValueError(
                "execution authorization cannot be issued "
                "for an ineligible deployment"
            )

        if validity_minutes <= 0:

            raise ValueError(
                "execution authorization validity duration "
                "must be greater than zero"
            )

        issued_at = (
            datetime
            .now(timezone.utc)
        )

        expires_at = (
            issued_at
            +
            timedelta(
                minutes=validity_minutes
            )
        )

        return DeploymentExecutionAuthorizationToken(

            token_id=
                str(uuid4()),

            deployment_id=
                deployment_id,

            artifact_digest=
                artifact_digest,

            environment=
                environment
                .strip()
                .lower(),

            issued_at=
                issued_at
                .isoformat(),

            expires_at=
                expires_at
                .isoformat()
        )

    def validate(
        self,
        token: DeploymentExecutionAuthorizationToken,
        deployment_id: str,
        artifact_digest: str,
        environment: str
    ):

        reasons = []

        now = (
            datetime
            .now(timezone.utc)
        )

        expires_at = (
            datetime
            .fromisoformat(
                token.expires_at
            )
        )

        if expires_at.tzinfo is None:

            expires_at = (
                expires_at
                .replace(
                    tzinfo=timezone.utc
                )
            )

        expired = (
            now > expires_at
        )

        deployment_matches = (
            token.deployment_id
            ==
            deployment_id
        )

        artifact_matches = (
            token.artifact_digest
            ==
            artifact_digest
        )

        environment_matches = (
            token.environment
            ==
            environment
            .strip()
            .lower()
        )

        if expired:

            reasons.append(
                "execution authorization token has expired"
            )

        if token.consumed:

            reasons.append(
                "execution authorization token "
                "has already been consumed"
            )

        if not deployment_matches:

            reasons.append(
                "deployment does not match "
                "the authorized deployment"
            )

        if not artifact_matches:

            reasons.append(
                "artifact does not match "
                "the authorized artifact"
            )

        if not environment_matches:

            reasons.append(
                "environment does not match "
                "the authorized environment"
            )

        valid = (
            not expired
            and not token.consumed
            and deployment_matches
            and artifact_matches
            and environment_matches
        )

        if valid:

            reasons.append(
                "execution authorization token "
                "is valid for this deployment"
            )

        return (
            DeploymentExecutionAuthorizationValidation(

                token_id=
                    token.token_id,

                valid=
                    valid,

                expired=
                    expired,

                consumed=
                    token.consumed,

                deployment_matches=
                    deployment_matches,

                artifact_matches=
                    artifact_matches,

                environment_matches=
                    environment_matches,

                reasons=
                    reasons
            )
        )

    def consume(
        self,
        token: DeploymentExecutionAuthorizationToken,
        deployment_id: str,
        artifact_digest: str,
        environment: str
    ):

        validation = (
            self
            .validate(
                token,
                deployment_id,
                artifact_digest,
                environment
            )
        )

        if not validation.valid:

            raise ValueError(
                "execution authorization token "
                "is not valid for this deployment"
            )

        token.consumed = (
            True
        )

        token.consumed_at = (
            datetime
            .now(timezone.utc)
            .isoformat()
        )

        return token
