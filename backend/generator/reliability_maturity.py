from dataclasses import dataclass


@dataclass
class ReliabilityMaturity:

    level: str

    score: int

    strengths: list[str]

    next_steps: list[str]


class ReliabilityMaturityEngine:

    def assess(
        self,
        scorecard,
        governance
    ):

        score = (
            scorecard.score
        )

        strengths = []

        next_steps = []

        if governance.compliant:

            strengths.append(
                "Reliability policies satisfied"
            )

        if score >= 95:

            level = "optimized"

            next_steps.append(
                "Continue reliability excellence"
            )

        elif score >= 85:

            level = "managed"

            next_steps.append(
                "Increase automation coverage"
            )

        elif score >= 75:

            level = "defined"

            next_steps.append(
                "Improve reliability governance"
            )

        else:

            level = "initial"

            next_steps.append(
                "Stabilize deployment process"
            )

        return ReliabilityMaturity(
            level=level,

            score=score,

            strengths=strengths,

            next_steps=next_steps
        )