from dataclasses import dataclass
from enum import Enum


class WorkflowLifecycleState(Enum):

    DRAFT = "draft"

    REVIEW = "review"

    APPROVED = "approved"

    DEPLOYED = "deployed"

    PAUSED = "paused"

    DEPRECATED = "deprecated"


@dataclass
class WorkflowLifecycle:

    workflow_id: str

    state: WorkflowLifecycleState

    execution_allowed: bool


class WorkflowLifecycleManagementEngine:

    def initialize(
        self,
        workflow_id: str
    ):

        return WorkflowLifecycle(

            workflow_id=
                workflow_id,

            state=
                WorkflowLifecycleState.DRAFT,

            execution_allowed=
                False
        )
