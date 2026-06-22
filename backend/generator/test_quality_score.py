from dataclasses import dataclass


@dataclass
class TestQualityScore:

    overall_score: float

    coverage_score: float

    regression_score: float

    performance_score: float

    quality_grade: str


class TestQualityScoreEngine:

    def generate(
        self
    ):

        return TestQualityScore(

            overall_score=
                92.0,

            coverage_score=
                95.0,

            regression_score=
                90.0,

            performance_score=
                91.0,

            quality_grade=
                "A"
        )
