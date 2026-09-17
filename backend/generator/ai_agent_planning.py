from dataclasses import dataclass


@dataclass
class AIAgentPlanning:

    planning_strategy: str

    reasoning_model: str

    task_decomposition_enabled: bool

    adaptive_replanning_enabled: bool


class AIAgentPlanningIntelligenceEngine:

    def generate(
        self
    ):

        return AIAgentPlanning(

            planning_strategy=
                "hierarchical_task_planning",

            reasoning_model=
                "goal_directed",

            task_decomposition_enabled=
                True,

            adaptive_replanning_enabled=
                True
        )
