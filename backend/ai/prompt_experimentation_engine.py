from dataclasses import dataclass


@dataclass
class PromptExperiment:

    experiment_id: str

    prompt_id: str

    candidate_version: str

    baseline_version: str


@dataclass
class ExperimentResult:

    winner: str

    completed: bool


class PromptExperimentationEngine:

    def create(
        self,
        prompt_id: str,
        baseline_version: str,
        candidate_version: str
    ):

        return PromptExperiment(

            experiment_id=
                "experiment-001",

            prompt_id=
                prompt_id,

            candidate_version=
                candidate_version,

            baseline_version=
                baseline_version
        )
