from dataclasses import dataclass


@dataclass
class ValidationIssue:

    severity: str

    message: str

    node_id: str | None


@dataclass
class WorkflowValidationResult:

    valid: bool

    issues: list[ValidationIssue]


class WorkflowValidationEngine:

    def validate(
        self,
        workflow
    ):

        return WorkflowValidationResult(

            valid=True,

            issues=[]
        )
