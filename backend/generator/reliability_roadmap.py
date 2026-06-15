from dataclasses import dataclass


@dataclass
class RoadmapMilestone:

    phase: int

    title: str

    objective: str


@dataclass
class ReliabilityRoadmap:

    current_level: str

    target_level: str

    milestones: list[RoadmapMilestone]


class ReliabilityRoadmapEngine:

    LEVELS = [
        "initial",
        "defined",
        "managed",
        "optimized"
    ]

    def generate(
        self,
        maturity
    ):

        current = (
            maturity.level
        )

        current_index = (
            self.LEVELS.index(
                current
            )
        )

        target = (
            self.LEVELS[-1]
        )

        milestones = []

        for phase, level in enumerate(
            self.LEVELS[
                current_index + 1:
            ],
            start=1
        ):

            milestones.append(

                RoadmapMilestone(

                    phase=phase,

                    title=
                        (
                            f"Reach "
                            f"{level}"
                        ),

                    objective=
                        (
                            f"Improve "
                            f"reliability "
                            f"toward "
                            f"{level}"
                        )
                )
            )

        return ReliabilityRoadmap(
            current_level=
                current,

            target_level=
                target,

            milestones=
                milestones
        )