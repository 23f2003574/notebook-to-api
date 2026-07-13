from dataclasses import dataclass
from typing import Dict, List


@dataclass
class DeploymentApprovalAuthorizationDecision:

    actor_id: str

    role: str

    environment: str

    required_permission: str

    authorized: bool

    reason: str


@dataclass
class AuthorizedDeploymentApprovalResult:

    authorization: (
        DeploymentApprovalAuthorizationDecision
    )

    approval_request: object


class DeploymentApprovalAuthorizationEngine:

    def __init__(self):

        self.role_permissions: Dict[
            str,
            List[str]
        ] = {

            "viewer": [
                "view_deployment"
            ],

            "developer": [
                "view_deployment",
                "request_deployment"
            ],

            "service_owner": [
                "view_deployment",
                "request_deployment",
                "approve_staging_deployment",
                "approve_production_deployment"
            ],

            "platform_admin": [
                "view_deployment",
                "request_deployment",
                "approve_staging_deployment",
                "approve_production_deployment"
            ]
        }

    def authorize(
        self,
        actor_id: str,
        role: str,
        environment: str
    ):

        normalized_role = (
            role
            .strip()
            .lower()
        )

        normalized_environment = (
            environment
            .strip()
            .lower()
        )

        required_permission = (
            f"approve_"
            f"{normalized_environment}_"
            f"deployment"
        )

        permissions = (
            self
            .role_permissions
            .get(
                normalized_role,
                []
            )
        )

        authorized = (
            required_permission
            in permissions
        )

        if authorized:

            reason = (
                "actor role grants the required "
                "deployment approval permission"
            )

        else:

            reason = (
                "actor role does not grant the required "
                "deployment approval permission"
            )

        return (
            DeploymentApprovalAuthorizationDecision(

                actor_id=
                    actor_id,

                role=
                    normalized_role,

                environment=
                    normalized_environment,

                required_permission=
                    required_permission,

                authorized=
                    authorized,

                reason=
                    reason
            )
        )
