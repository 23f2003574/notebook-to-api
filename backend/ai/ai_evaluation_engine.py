from dataclasses import dataclass


@dataclass
class EvaluationMetric:

    name: str

    score: float


@dataclass
class EvaluationReport:

    evaluation_id: str

    metrics: list[EvaluationMetric]

    overall_score: float


class AiEvaluationEngine:

    def evaluate(
        self,
        experiment_id: str
    ):

        return EvaluationReport(

            evaluation_id=
                "evaluation-001",

            metrics=[],

            overall_score=
                0.0
        )
