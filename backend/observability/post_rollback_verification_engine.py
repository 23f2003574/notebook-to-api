from dataclasses import dataclass


@dataclass
class PostRollbackVerification:

    deployment_id: str

    expected_version: str

    active_version: str

    health_check_passed: bool

    rollback_verified: bool


class PostRollbackVerificationEngine:

    def verify(
        self,
        deployment_id: str,
        expected_version: str,
        active_version: str,
        health_check_passed: bool
    ):

        rollback_verified = (
            expected_version == active_version
            and health_check_passed
        )

        return PostRollbackVerification(

            deployment_id=
                deployment_id,

            expected_version=
                expected_version,

            active_version=
                active_version,

            health_check_passed=
                health_check_passed,

            rollback_verified=
                rollback_verified
        )
