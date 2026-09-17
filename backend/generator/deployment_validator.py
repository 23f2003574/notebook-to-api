from .deployment_target_validators import (
    ValidationResult,
    DockerValidator,
    KubernetesValidator,
    TerraformValidator
)


class DeploymentValidator:

    def __init__(
        self
    ):

        self.docker_validator = (
            DockerValidator()
        )

        self.k8s_validator = (
            KubernetesValidator()
        )

        self.terraform_validator = (
            TerraformValidator()
        )

    def validate_artifact(
        self,
        target: str,
        content: str
    ):

        if not content:

            return ValidationResult(
                target=target,

                passed=False,

                message=
                    "Artifact is empty"
            )

        return ValidationResult(
            target=target,

            passed=True,

            message=
                "Validation passed"
        )

    def validate_target(
        self,
        target: str,
        content: str
    ):

        if target == "dockerfile":

            return (
                self.docker_validator
                .validate(
                    content
                )
            )

        if (
            "k8s"
            in target
        ):

            return (
                self.k8s_validator
                .validate(
                    content
                )
            )

        if (
            "terraform"
            in target
        ):

            return (
                self.terraform_validator
                .validate(
                    content
                )
            )

        return self.validate_artifact(
            target,
            content
        )