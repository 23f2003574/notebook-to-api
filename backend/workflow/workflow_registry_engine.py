from dataclasses import dataclass


@dataclass
class WorkflowRegistration:

    workflow_id: str

    name: str

    active_version: str

    status: str


class WorkflowRegistryEngine:

    def register(
        self,
        workflow_id: str,
        name: str
    ):

        return WorkflowRegistration(

            workflow_id=
                workflow_id,

            name=
                name,

            active_version=
                "1.0.0",

            status=
                "active"
        )
