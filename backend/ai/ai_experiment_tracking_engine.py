from dataclasses import dataclass


@dataclass
class AiExperimentRun:

    experiment_id: str

    prompt_version: str

    model_id: str

    dataset_id: str

    evaluation_id: str


class AiExperimentTrackingEngine:

    def track(
        self,
        experiment_id: str,
        prompt_version: str,
        model_id: str,
        dataset_id: str,
        evaluation_id: str
    ):

        return AiExperimentRun(

            experiment_id=
                experiment_id,

            prompt_version=
                prompt_version,

            model_id=
                model_id,

            dataset_id=
                dataset_id,

            evaluation_id=
                evaluation_id
        )
