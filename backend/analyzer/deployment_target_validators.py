from dataclasses import dataclass


@dataclass
class ValidationResult:

    target: str

    passed: bool

    message: str


class DockerValidator:

    def validate(
        self,
        content: str
    ):

        passed = (
            "FROM"
            in content
        )

        return ValidationResult(
            target="docker",

            passed=passed,

            message=(
                "Dockerfile valid"
                if passed
                else
                "Missing FROM"
            )
        )


class KubernetesValidator:

    def validate(
        self,
        content: str
    ):

        passed = (
            "apiVersion"
            in content
        )

        return ValidationResult(
            target="kubernetes",

            passed=passed,

            message=(
                "Kubernetes manifest valid"
                if passed
                else
                "Missing apiVersion"
            )
        )


class TerraformValidator:

    def validate(
        self,
        content: str
    ):

        passed = (
            "terraform"
            in content
        )

        return ValidationResult(
            target="terraform",

            passed=passed,

            message=(
                "Terraform valid"
                if passed
                else
                "Missing terraform block"
            )
        )