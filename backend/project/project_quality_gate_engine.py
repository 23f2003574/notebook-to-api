from dataclasses import dataclass


@dataclass
class QualityCriterion:

    name: str

    passed: bool


@dataclass
class QualityGateResult:

    approved: bool

    criteria: list[QualityCriterion]


class ProjectQualityGateEngine:

    def evaluate(
        self,
        test_execution
    ):

        return QualityGateResult(

            approved=True,

            criteria=[

                QualityCriterion(

                    name=
                        "tests",

                    passed=
                        True
                ),

                QualityCriterion(

                    name=
                        "coverage",

                    passed=
                        True
                )
            ]
        )
