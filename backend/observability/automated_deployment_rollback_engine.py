from dataclasses import dataclass


@dataclass
class DeploymentRollback:

    deployment_id: str

    failed_version: str

    previous_stable_version: str

    rollback_required: bool

    rollback_status: str


class AutomatedDeploymentRollbackEngine:

    def evaluate(
        self,
        deployment_id: str,
        failed_version: str,
        previous_stable_version: str,
        deployment_healthy: bool
    ):

        rollback_required = (
            not deployment_healthy
        )

        rollback_status = (
            "initiated"
            if rollback_required
            else "not_required"
        )

        return DeploymentRollback(

            deployment_id=
                deployment_id,

            failed_version=
                failed_version,

            previous_stable_version=
                previous_stable_version,

            rollback_required=
                rollback_required,

            rollback_status=
                rollback_status
        )
