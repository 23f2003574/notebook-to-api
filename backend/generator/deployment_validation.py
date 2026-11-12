import re
from dataclasses import dataclass, field

from .deployment_validator import DeploymentValidator
from .manifest_validator import (
    ManifestValidationError,
    validate_compose_manifest,
    validate_kubernetes_manifest,
)

# "NAME=value" pairs in a validator message could echo an environment entry.
_ASSIGNMENT = re.compile(r"(\b[A-Za-z_][A-Za-z0-9_]*)=[^\s',\]}]+")


@dataclass
class DeploymentValidation:

    validation_passed: bool

    checks_performed: int

    warnings: list[str]

    # Additive: why validation_passed is False (empty when it passed).
    errors: list[str] = field(default_factory=list)


def _redact(message: str) -> str:
    return _ASSIGNMENT.sub(r"\1=***", message)


class DeploymentValidationEngine:
    """Runs the existing validators over the deployment artifacts supplied.

    ``artifacts`` maps a file name (``Dockerfile``, ``docker-compose.yml``,
    ``kubernetes.yaml``) to its text. Every artifact a validator was run on counts
    as one check; unrecognised names are skipped with a warning, never counted.
    """

    def generate(
        self,
        artifacts=None
    ):

        if not isinstance(artifacts, dict) or not artifacts:
            return DeploymentValidation(
                validation_passed=False,
                checks_performed=0,
                warnings=[],
                errors=["no deployment artifacts were supplied, so nothing could be validated"],
            )

        checks = 0
        warnings = []
        errors = []

        for name, content in artifacts.items():
            lowered = str(name).lower()
            if not isinstance(content, str) or not content.strip():
                checks += 1
                errors.append(f"{name}: artifact is missing or empty")
                continue
            try:
                if lowered.startswith("dockerfile"):
                    result = DeploymentValidator().validate_target("dockerfile", content)
                    if not result.passed:
                        raise ManifestValidationError(f"{name}: {result.message}")
                elif "compose" in lowered:
                    validate_compose_manifest(content)
                elif "kubernetes" in lowered or "k8s" in lowered:
                    validate_kubernetes_manifest(content)
                else:
                    warnings.append(f"{name}: no validator for this artifact; skipped")
                    continue
            except ManifestValidationError as exc:
                errors.append(_redact(str(exc)))
            except Exception as exc:  # a crashing validator must not read as success
                errors.append(f"{name}: validation could not run ({type(exc).__name__})")
            checks += 1

        return DeploymentValidation(
            validation_passed=not errors and checks > 0,
            checks_performed=checks,
            warnings=warnings,
            errors=errors if checks else errors or ["no recognised deployment artifacts were supplied"],
        )
