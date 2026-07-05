from dataclasses import dataclass


@dataclass
class WorkflowVersion:

    workflow_id: str

    version: str

    checksum: str

    active: bool


class WorkflowVersioningEngine:

    def create_version(
        self,
        workflow_id: str
    ):

        return WorkflowVersion(

            workflow_id=
                workflow_id,

            version=
                "1.0.0",

            checksum=
                "sha256-placeholder",

            active=
                True
        )
