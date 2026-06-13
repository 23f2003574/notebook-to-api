from dataclasses import dataclass


@dataclass
class ValidationResult:

    target: str

    passed: bool

    message: str


class DeploymentValidator:

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