from dataclasses import dataclass


@dataclass
class DeploymentAudit:

    compliant: bool

    validation_passed: bool

    deployment_ready: bool

    findings: list[str]


class DeploymentAuditGenerator:

    def generate(
        self,
        readiness,
        validation_results
    ):

        validation_passed = all(
            result.passed
            for result
            in validation_results
        )

        deployment_ready = (
            readiness.ready
        )

        findings = []

        if validation_passed:

            findings.append(
                "All validations passed"
            )

        if deployment_ready:

            findings.append(
                "Deployment ready"
            )

        compliant = (
            validation_passed
            and
            deployment_ready
        )

        return DeploymentAudit(
            compliant=
                compliant,

            validation_passed=
                validation_passed,

            deployment_ready=
                deployment_ready,

            findings=
                findings
        )